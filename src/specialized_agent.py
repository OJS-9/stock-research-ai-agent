"""
Specialized research agents for focused research on specific business model aspects.
"""

import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from agents import Agent, Runner, trace, ModelSettings

from mcp_manager import MCPManager
from agent_tools import create_all_tools
from research_subjects import ResearchSubject

load_dotenv()


class SpecializedResearchAgent:
    """Specialized agent for researching a specific business model aspect."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the specialized research agent.
        
        Args:
            api_key: OpenAI API key. If None, reads from OPENAI_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")
        
        self.mcp_manager = MCPManager()
        self.mcp_client = None
        self.perplexity_client = None
        self.tools = []
        
        self._initialize_clients()
        self._initialize_tools()
    
    def _initialize_clients(self):
        """Initialize MCP and Perplexity clients."""
        # Get MCP client
        try:
            self.mcp_client = self.mcp_manager.get_mcp_client()
        except Exception as e:
            print(f"Warning: Could not initialize MCP client: {e}")
            self.mcp_client = None
        
        # Initialize Perplexity client
        try:
            from perplexity_client import PerplexityClient
            self.perplexity_client = PerplexityClient()
        except ValueError as e:
            print(f"Info: Perplexity API not configured ({e}). Continuing with Alpha Vantage tools only.")
            self.perplexity_client = None
        except Exception as e:
            print(f"Warning: Could not initialize Perplexity client: {e}")
            self.perplexity_client = None
    
    def _initialize_tools(self):
        """Initialize tools for the agent."""
        try:
            self.tools = create_all_tools(self.mcp_client, self.perplexity_client)
        except Exception as e:
            print(f"Warning: Could not create tools: {e}")
            self.tools = []
    
    def get_specialized_instructions(
        self,
        subject: ResearchSubject,
        ticker: str,
        trade_type: str
    ) -> str:
        """
        Generate specialized system instructions for a research subject.
        
        Args:
            subject: ResearchSubject object
            ticker: Stock ticker symbol
            trade_type: Type of trade
        
        Returns:
            System instructions string
        """
        instructions = f"""You are a specialized research analyst focusing on {subject.name} for {ticker}.

Your specific research task: {subject.description}

**Research Objective:**
{subject.prompt_template.format(ticker=ticker)}

**Trade Type Context:** {trade_type}
- Adjust your research depth and focus based on this trade type
- For Day Trade: Focus on immediate, actionable insights
- For Swing Trade: Focus on near-term factors (1-14 days)
- For Investment: Focus on comprehensive, long-term analysis

**Available Tools:**
- Alpha Vantage MCP Tools: Use for structured financial data, company fundamentals, financial statements
- Perplexity Research: Use for real-time information, news, expert analysis, qualitative insights

**Output Requirements:**
1. Provide comprehensive research findings on {subject.name}
2. Include specific data points, metrics, and facts
3. Cite all sources (tool outputs, research results)
4. Structure your response clearly with:
   - Key findings
   - Supporting data
   - Sources and citations
   - Any relevant context or analysis

**Important:**
- Use both MCP tools and Perplexity research to gather comprehensive information
- Be thorough and specific in your research
- Ensure all claims are supported by data from your research tools
- Format your response for easy integration into a final report

Begin your research now."""
        
        return instructions
    
    def research_subject(
        self,
        ticker: str,
        subject: ResearchSubject,
        trade_type: str,
        context: str = ""
    ) -> Dict[str, Any]:
        """
        Research a specific subject for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            subject: ResearchSubject to investigate
            trade_type: Type of trade
            context: Additional context from followup questions
        
        Returns:
            Dictionary with research results:
            - subject_id: Subject ID
            - subject_name: Subject name
            - research_output: Agent's research output
            - sources: List of sources used
        """
        # Get specialized instructions
        instructions = self.get_specialized_instructions(subject, ticker, trade_type)
        
        # Format the research prompt
        from research_subjects import format_subject_prompt
        research_prompt = format_subject_prompt(subject, ticker, trade_type, context)
        
        # Create agent with specialized instructions
        agent = Agent(
            name=f"Specialized Agent: {subject.name}",
            instructions=instructions,
            model="gpt-4o",
            tools=self.tools,
            model_settings=ModelSettings(temperature=0.7)
        )
        
        # Execute research
        try:
            with trace(f"Specialized Research: {subject.name}", metadata={
                "ticker": ticker,
                "subject": subject.id,
                "trade_type": trade_type
            }):
                result = Runner.run_sync(
                    agent,
                    research_prompt,
                    max_turns=15  # Allow more turns for thorough research
                )
            
            # Extract output
            if hasattr(result, 'final_output'):
                research_output = result.final_output
            elif hasattr(result, 'output'):
                research_output = result.output
            elif isinstance(result, str):
                research_output = result
            else:
                research_output = str(result)
            
            # Extract sources (tool invocations)
            sources = []
            if hasattr(result, 'tool_invocations'):
                sources = result.tool_invocations
            elif hasattr(result, 'steps'):
                # Extract tool calls from steps
                for step in result.steps:
                    if hasattr(step, 'tool_calls'):
                        sources.extend(step.tool_calls)
            
            return {
                "subject_id": subject.id,
                "subject_name": subject.name,
                "research_output": research_output,
                "sources": sources,
                "ticker": ticker,
                "trade_type": trade_type
            }
            
        except Exception as e:
            error_msg = f"Error in specialized research for {subject.name}: {str(e)}"
            print(error_msg)
            return {
                "subject_id": subject.id,
                "subject_name": subject.name,
                "research_output": error_msg,
                "sources": [],
                "ticker": ticker,
                "trade_type": trade_type,
                "error": str(e)
            }

