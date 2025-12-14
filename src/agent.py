"""
OpenAI Agents SDK agent setup with MCP integration.
"""

import os
import json
import re
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from agents import Agent, Runner, Tool, trace, ModelSettings

from mcp_manager import MCPManager
from research_prompt import get_system_instructions, get_followup_question_prompt
from agent_tools import create_all_tools
from research_orchestrator import ResearchOrchestrator
from synthesis_agent import SynthesisAgent
from report_storage import ReportStorage
from report_chat_agent import ReportChatAgent

# Load environment variables
load_dotenv()


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
        
        self.mcp_manager = MCPManager()
        self.agent = None
        self.conversation_history: List[Dict[str, str]] = []
        self.use_fallback = False
        self.current_ticker: Optional[str] = None
        self.current_trade_type: Optional[str] = None
        self.current_report_id: Optional[str] = None
        self.research_orchestrator = ResearchOrchestrator(api_key=self.api_key)
        self.synthesis_agent = SynthesisAgent(api_key=self.api_key)
        self.report_storage = ReportStorage()
        self.chat_agent = ReportChatAgent(api_key=self.api_key)
        
        # Initialize the agent
        self._initialize_agent()
    
    def _initialize_agent(self):
        """Initialize the agent with MCP connection and Perplexity client using Agents SDK."""
        # Get MCP client
        try:
            self.mcp_client = self.mcp_manager.get_mcp_client()
            print(f"✓ Connected to MCP server")
        except Exception as e:
            print(f"Warning: Could not initialize MCP client: {e}")
            self.mcp_client = None
        
        # Initialize Perplexity client
        try:
            from perplexity_client import PerplexityClient
            
            self.perplexity_client = PerplexityClient()
            print("✓ Loaded Perplexity client")
        except ValueError as e:
            # Graceful fallback if Perplexity API key not set
            print(f"Info: Perplexity API not configured ({e}). Continuing with Alpha Vantage tools only.")
            self.perplexity_client = None
        except Exception as e:
            print(f"Warning: Could not initialize Perplexity client: {e}")
            self.perplexity_client = None
        
        # Create all tools using Agents SDK format
        try:
            all_tools = create_all_tools(self.mcp_client, self.perplexity_client)
            print(f"✓ Loaded {len(all_tools)} tools for Agents SDK")
        except Exception as e:
            print(f"Warning: Could not create tools: {e}")
            all_tools = []
        
        # Create Agent instance with Agents SDK
        # Instructions will be set per-run via system message
        self.agent = Agent(
            name="Stock Research Agent",
            instructions="You are a helpful stock research assistant. Use available tools to gather financial data and research information.",
            model="gpt-4o",
            tools=all_tools,
            model_settings=ModelSettings(temperature=0.7)
        )
        
        # Runner is used as a class with static methods, no instance needed
    
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
        
        # Get system instructions
        system_instructions = get_system_instructions(ticker, trade_type)
        
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
        Get response from the agent with MCP tool support.
        
        Args:
            user_message: Current user message
            system_instructions: System instructions for the agent
        
        Returns:
            Agent's response
        """
        # Use OpenAI API with function calling for MCP tools
        return self._get_response_with_tools(user_message, system_instructions)
    
    def _get_response_with_tools(self, user_message: str, system_instructions: str) -> str:
        """
        Get agent response with MCP tool calling support using Agents SDK.
        
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
            # Agents SDK Runner accepts a list of messages or a single message string
            # We'll pass the user message and let the agent use system instructions
            
            # Update agent instructions with system instructions for this run
            # Note: We'll pass system message as part of the conversation
            messages = []
            
            # Add conversation history (excluding system messages, as we'll set instructions separately)
            for msg in self.conversation_history[-10:]:  # Keep last 10 messages
                if msg["role"] != "system":
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })
            
            # Add current user message
            messages.append({
                "role": "user",
                "content": user_message
            })
            
            # Update agent instructions with system instructions
            # We'll create a temporary agent with updated instructions
            agent_with_instructions = Agent(
                name=self.agent.name,
                instructions=system_instructions,
                model=self.agent.model,
                tools=self.agent.tools,
                model_settings=self.agent.model_settings
            )
            
            # Wrap execution with trace for monitoring
            with trace("Stock Research Agent Run", metadata={
                "ticker": self._extract_ticker_from_history(),
                "trade_type": self._extract_trade_type_from_history()
            }):
                # Run agent with Runner
                result = Runner.run_sync(
                    agent_with_instructions,
                    messages if len(messages) > 1 else user_message,
                    max_turns=10  # Prevent infinite loops
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
                match = re.search(r'\b([A-Z]{1,5})\b', content)
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
                context=context
            )
            
            # Step 2: Synthesize report
            print(f"\n{'='*60}")
            print("Synthesizing research findings into final report...")
            print(f"{'='*60}\n")
            
            report_text = self.synthesis_agent.synthesize_report(
                ticker=ticker,
                trade_type=trade_type,
                research_outputs=research_outputs,
                context=context
            )
            
            # Step 3: Store report with chunks and embeddings
            print(f"\n{'='*60}")
            print("Storing report with chunking and embeddings...")
            print(f"{'='*60}\n")
            
            metadata = {
                "trade_type": trade_type,
                "research_subjects": list(research_outputs.keys()),
                "context": context
            }
            
            report_id = self.report_storage.store_report(
                ticker=ticker,
                trade_type=trade_type,
                report_text=report_text,
                metadata=metadata
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
            question=question
        )
    
    def reset_conversation(self):
        """Reset the conversation history."""
        self.conversation_history = []
        self.current_ticker = None
        self.current_trade_type = None
        self.current_report_id = None
        self.chat_agent.reset_conversation()


def create_agent(api_key: Optional[str] = None) -> StockResearchAgent:
    """
    Create a new stock research agent instance.
    
    Args:
        api_key: OpenAI API key (optional, will use env var if not provided)
    
    Returns:
        StockResearchAgent instance
    """
    return StockResearchAgent(api_key=api_key)

