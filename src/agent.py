"""
OpenAI Agents SDK agent setup with MCP integration.
"""

import os
import json
import re
import time
from typing import Optional, Dict, Any, List, Any as AnyType
from dotenv import load_dotenv

from agents import Agent, Runner, Tool, trace, ModelSettings
from agents.tool import FunctionTool

from research_prompt import get_orchestration_instructions, get_followup_question_prompt
from research_orchestrator import ResearchOrchestrator
from synthesis_agent import SynthesisAgent
from report_storage import ReportStorage
from report_chat_agent import ReportChatAgent

# Load environment variables
load_dotenv()

# Orchestrator configuration (token & turn limits, history controls)
ORCHESTRATOR_MAX_OUTPUT_TOKENS = int(
    os.getenv("ORCHESTRATOR_MAX_OUTPUT_TOKENS", "600")
)
ORCHESTRATOR_MAX_TURNS = int(
    os.getenv("ORCHESTRATOR_MAX_TURNS", "6")
)
ORCHESTRATOR_MAX_HISTORY_MESSAGES = int(
    os.getenv("ORCHESTRATOR_MAX_HISTORY_MESSAGES", "4")
)
ORCHESTRATOR_MAX_MESSAGE_CHARS = int(
    os.getenv("ORCHESTRATOR_MAX_MESSAGE_CHARS", "1000")
)
ORCHESTRATOR_DEBUG_TOKEN_LOG = os.getenv(
    "ORCHESTRATOR_DEBUG_TOKEN_LOG", "false"
).lower() == "true"


class StockResearchAgent:
    """Stock research agent using OpenAI Agents SDK with Alpha Vantage MCP."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the stock research agent.
        
        Args:
            api_key: OpenAI API key. If None, reads from OPENAI_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")
        
        self.agent = None
        self.conversation_history: List[Dict[str, str]] = []
        self.use_fallback = False
        self.current_ticker: Optional[str] = None
        self.current_trade_type: Optional[str] = None
        self.current_report_id: Optional[str] = None
        self.last_report_text: Optional[str] = None
        self.research_orchestrator = ResearchOrchestrator(api_key=self.api_key)
        self.synthesis_agent = SynthesisAgent(api_key=self.api_key)
        self.report_storage = ReportStorage()
        self.chat_agent = ReportChatAgent(api_key=self.api_key)
        
        # Initialize the agent
        self._initialize_agent()
    
    def _initialize_agent(self):
        """Initialize the orchestration agent with report generation tool."""
        # Create tool for triggering report generation
        generate_report_tool = self._create_generate_report_tool()
        
        # Create Agent instance with Agents SDK
        # Main agent has one orchestration tool: generate_report
        # Instructions will be set per-run via system message
        self.agent = Agent(
            name="Stock Research Orchestrator",
            instructions="You are a stock research orchestrator. Your role is to guide conversations, ask clarifying questions, and coordinate research. You do not perform research yourself - specialized agents handle that.",
            model="gpt-4o",
            tools=[generate_report_tool],  # Only orchestration tool: generate_report
            model_settings=ModelSettings(
                temperature=0.7,
                max_output_tokens=ORCHESTRATOR_MAX_OUTPUT_TOKENS,
            ),
        )
    
    def _create_generate_report_tool(self) -> FunctionTool:
        """Create a tool that allows the orchestrator to trigger report generation."""
        async def generate_report_tool_function(context, tool_input: Dict[str, Any]) -> str:
            """
            Trigger report generation when orchestrator has enough information.
            
            Args:
                context: Tool context (provided by SDK)
                tool_input: Tool input (can be empty dict, context is extracted from conversation)
            
            Returns:
                Status message about report generation
            """
            try:
                # Extract context from conversation history
                context_str = ""
                for msg in self.conversation_history:
                    if msg.get('role') == 'user':
                        context_str += f"User: {msg.get('content', '')}\n"
                
                # Generate report (this is synchronous but called from async function - OK)
                report_text = self.generate_report(context=context_str)
                report_id = self.current_report_id
                
                # Store report text for Flask session access
                self.last_report_text = report_text

                return f"Report generated successfully! Report ID: {report_id[:8] if report_id else 'N/A'}...\n\nThe comprehensive research report has been created. You can inform the user that the report is ready."
            except Exception as e:
                return f"Error generating report: {str(e)}"
        
        # Create tool schema
        tool_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        
        return FunctionTool(
            name="generate_report",
            description=(
                "Trigger report generation when you have gathered enough information from the user. "
                "Use this tool when you have asked 1-2 relevant questions and the user has provided sufficient context. "
                "This will activate specialized research agents to perform comprehensive analysis and generate the final report. "
                "Only call this tool when you are ready to proceed - don't call it immediately after asking questions."
            ),
            params_json_schema=tool_schema,
            on_invoke_tool=generate_report_tool_function,
        )
    
    def start_research(self, ticker: str, trade_type: str) -> str:
        """
        Start a research session for a given ticker and trade type.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade (Day Trade, Swing Trade, or Investment)
        
        Returns:
            Initial response from the agent (may include follow-up questions)
        """
        self.current_ticker = ticker.upper()
        self.current_trade_type = trade_type
        
        # Get orchestration instructions
        system_instructions = get_orchestration_instructions(ticker, trade_type)
        
        # Create initial user message
        user_message = f"I want to research {ticker} for a {trade_type} strategy. Please help me create a fundamental research report."
        
        # Store in conversation history
        self.conversation_history = [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": user_message}
        ]
        
        # Get agent response (may ask followup questions)
        response = self._get_agent_response(user_message, system_instructions)
        
        return response
    
    def continue_conversation(self, user_response: str) -> str:
        """
        Continue the conversation with a user response.
        
        Args:
            user_response: User's response to agent's question or instruction
        
        Returns:
            Agent's response
        """
        # Add user message to history
        self.conversation_history.append({"role": "user", "content": user_response})
        
        # Get system instructions from history
        system_instructions = next(
            (msg["content"] for msg in self.conversation_history if msg["role"] == "system"),
            ""
        )
        
        # Get agent response
        response = self._get_agent_response(user_response, system_instructions)
        
        return response
    
    def _get_agent_response(self, user_message: str, system_instructions: str) -> str:
        """
        Get agent response (orchestration agent - no tools needed).
        
        Args:
            user_message: Current user message
            system_instructions: System instructions
        
        Returns:
            Agent's response
        """
        if not self.agent:
            return "Error: Agent not initialized. Please check your configuration."
        
        try:
            # Build messages for Runner
            messages: List[Dict[str, Any]] = []

            # Add recent conversation history (excluding system messages; we'll set instructions separately)
            recent_history = self.conversation_history[-ORCHESTRATOR_MAX_HISTORY_MESSAGES:]
            for msg in recent_history:
                if msg["role"] != "system":
                    content = msg["content"]
                    if isinstance(content, str) and len(content) > ORCHESTRATOR_MAX_MESSAGE_CHARS:
                        content = content[:ORCHESTRATOR_MAX_MESSAGE_CHARS] + "... [truncated]"
                    messages.append({
                        "role": msg["role"],
                        "content": content,
                    })

            # Add current user message (also truncated defensively)
            current_content = user_message
            if isinstance(current_content, str) and len(current_content) > ORCHESTRATOR_MAX_MESSAGE_CHARS:
                current_content = current_content[:ORCHESTRATOR_MAX_MESSAGE_CHARS] + "... [truncated]"
            messages.append({
                "role": "user",
                "content": current_content,
            })

            # Create agent with updated instructions (include generate_report tool)
            agent_with_instructions = Agent(
                name=self.agent.name,
                instructions=system_instructions,
                model=self.agent.model,
                tools=self.agent.tools,  # Include generate_report tool
                model_settings=self.agent.model_settings
            )

            # Optional debug logging for approximate token usage
            if ORCHESTRATOR_DEBUG_TOKEN_LOG:
                approx_input_chars = len(str(messages))
                print(
                    f"[Orchestrator] Approx input chars: {approx_input_chars}, "
                    f"history_messages={len(recent_history)}"
                )

            # Wrap execution with trace for monitoring
            with trace("Stock Research Agent Run", metadata={
                "ticker": self._extract_ticker_from_history(),
                "trade_type": self._extract_trade_type_from_history()
            }):
                # Run agent with Runner using a small retry/backoff on rate limits
                result = _run_agent_with_retry(
                    agent_with_instructions,
                    messages if len(messages) > 1 else current_content,
                    max_turns=ORCHESTRATOR_MAX_TURNS,
                )
            
            # Extract final output from result
            if hasattr(result, 'final_output'):
                assistant_message = result.final_output
            elif hasattr(result, 'output'):
                assistant_message = result.output
            elif isinstance(result, str):
                assistant_message = result
            else:
                assistant_message = str(result)

            # Optional debug logging for approximate output size
            if ORCHESTRATOR_DEBUG_TOKEN_LOG:
                approx_output_chars = len(str(assistant_message))
                print(f"[Orchestrator] Approx output chars: {approx_output_chars}")

            # Update conversation history
            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            return assistant_message
            
        except Exception as e:
            error_msg = f"Error generating response: {str(e)}"
            print(f"Agent execution error: {e}")
            return error_msg
    
    def _extract_ticker_from_history(self) -> str:
        """Extract ticker from conversation history if available."""
        for msg in self.conversation_history:
            if msg["role"] == "system" and "ticker" in msg.get("content", "").lower():
                # Try to extract ticker from system message
                content = msg.get("content", "")
                # Simple extraction - look for patterns like "AAPL" or "ticker: AAPL"
                match = re.search(r"\b([A-Z]{1,5})\b", content)
                if match:
                    return match.group(1)
        return "unknown"
    
    def _extract_trade_type_from_history(self) -> str:
        """Extract trade type from conversation history if available."""
        for msg in self.conversation_history:
            if msg["role"] == "system":
                content = msg.get("content", "").lower()
                if "day trade" in content:
                    return "Day Trade"
                elif "swing trade" in content:
                    return "Swing Trade"
                elif "investment" in content:
                    return "Investment"
        return "unknown"
    
    def generate_report(self, context: str = "") -> str:
        """
        Generate a research report using parallel agents after followup questions are answered.
        
        Args:
            context: Additional context from followup questions
        
        Returns:
            Generated report text and report_id
        """
        if not self.current_ticker or not self.current_trade_type:
            return "Error: No active research session. Please start research first."
        
        ticker = self.current_ticker
        trade_type = self.current_trade_type
        
        print(f"\n{'='*60}")
        print(f"Starting parallel research for {ticker} ({trade_type})")
        print(f"{'='*60}\n")
        
        try:
            # Step 1: Run parallel research
            research_outputs = self.research_orchestrator.run_parallel_research(
                ticker=ticker,
                trade_type=trade_type,
                context=context,
            )
            
            # Step 2: Synthesize report
            print(f"\n{'='*60}")
            print("Synthesizing research findings into final report...")
            print(f"{'='*60}\n")
            
            report_text = self.synthesis_agent.synthesize_report(
                ticker=ticker,
                trade_type=trade_type,
                research_outputs=research_outputs,
                context=context,
            )
            
            # Step 3: Store report with chunks and embeddings
            print(f"\n{'='*60}")
            print("Storing report with chunking and embeddings...")
            print(f"{'='*60}\n")
            
            metadata = {
                "trade_type": trade_type,
                "research_subjects": list(research_outputs.keys()),
                "context": context,
            }
            
            report_id = self.report_storage.store_report(
                ticker=ticker,
                trade_type=trade_type,
                report_text=report_text,
                metadata=metadata,
            )
            
            self.current_report_id = report_id
            
            print(f"\n{'='*60}")
            print(f"✓ Report generated and stored: {report_id}")
            print(f"{'='*60}\n")
            
            return report_text
        
        except Exception as e:
            error_msg = f"Error generating report: {str(e)}"
            print(error_msg)
            return error_msg
    
    def chat_with_report(self, question: str) -> str:
        """
        Chat with the current report using RAG-lite.
        
        Args:
            question: User's question about the report
        
        Returns:
            Agent's answer based on report content
        """
        if not self.current_report_id:
            return "Error: No report available. Please generate a report first."
        
        return self.chat_agent.chat_with_report(
            report_id=self.current_report_id,
            question=question,
        )
    
    def reset_conversation(self):
        """Reset the conversation history."""
        self.conversation_history = []
        self.current_ticker = None
        self.current_trade_type = None
        self.current_report_id = None
        self.chat_agent.reset_conversation()


def _is_rate_limit_error(exc: Exception) -> bool:
    """Heuristic check for OpenAI-style rate limit errors (429 / rate_limit)."""
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    message = str(exc).lower()
    return "rate limit" in message or "429" in message


def _run_agent_with_retry(
    agent: Agent,
    messages_or_prompt: AnyType,
    max_turns: int,
) -> AnyType:
    """
    Run an agent with a small retry/backoff loop for rate limit errors.
    
    This keeps the logic localized and avoids leaking retries into callers.
    """
    max_retries = int(os.getenv("AGENT_RATE_LIMIT_MAX_RETRIES", "3"))
    base_delay = float(os.getenv("AGENT_RATE_LIMIT_BACKOFF_SECONDS", "2.0"))
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            return Runner.run_sync(agent, messages_or_prompt, max_turns=max_turns)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            is_rate_limit = _is_rate_limit_error(exc)
            is_last_attempt = attempt == max_retries - 1
            if not is_rate_limit or is_last_attempt:
                raise
            delay = base_delay * (2**attempt)
            print(
                f"[Orchestrator] Rate limit encountered, retrying in {delay:.1f}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(delay)

    # Should not reach here, but raise the last exception if we do
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unknown error in _run_agent_with_retry")


def create_agent(api_key: Optional[str] = None) -> StockResearchAgent:
    """
    Create a new stock research agent instance.
    
    This is also a key point for debugging how the agent is constructed.
    
    Args:
        api_key: OpenAI API key (optional, will use env var if not provided)
    
    Returns:
        StockResearchAgent instance
    """
    return StockResearchAgent(api_key=api_key)

