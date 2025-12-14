"""
OpenAI Agents SDK agent setup with MCP integration.
"""

import os
import json
import asyncio
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from src.mcp_manager import MCPManager
from src.research_prompt import get_system_instructions, get_followup_question_prompt
from src.mcp_tools import get_openai_function_definitions, execute_tool_by_name

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
        
        # Initialize the agent
        self._initialize_agent()
    
    def _initialize_agent(self):
        """Initialize the agent with MCP connection and Perplexity client."""
        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key)
        self.model = "gpt-4o"
        
        # Get MCP client and tools
        try:
            mcp_client = self.mcp_manager.get_mcp_client()
            self.mcp_tools = get_openai_function_definitions(mcp_client)
            self.mcp_client = mcp_client
            print(f"✓ Loaded {len(self.mcp_tools)} MCP tools")
        except Exception as e:
            print(f"Warning: Could not initialize MCP tools: {e}")
            self.mcp_tools = []
            self.mcp_client = None
        
        # Initialize Perplexity client and tool
        try:
            from src.perplexity_client import PerplexityClient
            from src.perplexity_tools import get_perplexity_research_function
            
            self.perplexity_client = PerplexityClient()
            self.perplexity_tool = get_perplexity_research_function()
            print("✓ Loaded Perplexity research tool")
        except ValueError as e:
            # Graceful fallback if Perplexity API key not set
            print(f"Info: Perplexity API not configured ({e}). Continuing with Alpha Vantage tools only.")
            self.perplexity_client = None
            self.perplexity_tool = None
        except Exception as e:
            print(f"Warning: Could not initialize Perplexity client: {e}")
            self.perplexity_client = None
            self.perplexity_tool = None
        
        # Combine all available tools
        self.all_tools = self.mcp_tools.copy()
        if self.perplexity_tool:
            self.all_tools.append(self.perplexity_tool)
    
    def start_research(self, ticker: str, trade_type: str) -> str:
        """
        Start a research session for a given ticker and trade type.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade (Day Trade, Swing Trade, or Investment)
        
        Returns:
            Initial response from the agent (may include follow-up questions)
        """
        # Get system instructions
        system_instructions = get_system_instructions(ticker, trade_type)
        
        # Create initial user message
        user_message = f"I want to research {ticker} for a {trade_type} strategy. Please help me create a fundamental research report."
        
        # Store in conversation history
        self.conversation_history = [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": user_message}
        ]
        
        # Get agent response
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
        Get agent response with MCP tool calling support.
        
        Args:
            user_message: Current user message
            system_instructions: System instructions
        
        Returns:
            Agent's response
        """
        # Build messages for API call
        messages = [
            {"role": "system", "content": system_instructions},
        ]
        
        # Add conversation history (last few messages for context)
        for msg in self.conversation_history[-10:]:  # Keep last 10 messages
            if msg["role"] != "system":
                messages.append(msg)
        
        # Add current user message if not already in history
        if not any(msg.get("content") == user_message for msg in messages):
            messages.append({"role": "user", "content": user_message})
        
        max_iterations = 5  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            try:
                # Prepare tools for API call (use all_tools which includes both MCP and Perplexity)
                tools = self.all_tools if self.all_tools else None
                
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",  # Let the model decide when to use tools
                    temperature=0.7,
                )
                
                message = response.choices[0].message
                
                # Add assistant message to conversation
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": message.tool_calls if hasattr(message, 'tool_calls') and message.tool_calls else None
                })
                
                # Check if the model wants to call a tool
                if message.tool_calls:
                    # Execute tool calls
                    for tool_call in message.tool_calls:
                        try:
                            # Execute tool call (routes to appropriate handler)
                            tool_result_str = self._execute_tool_call(tool_call)
                            
                            # Add tool result to messages
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "content": tool_result_str
                            })
                        except Exception as e:
                            error_msg = f"Error executing tool {tool_call.function.name}: {str(e)}"
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "content": error_msg
                            })
                    
                    # Continue the conversation to get final response
                    iteration += 1
                    continue
                else:
                    # No tool calls, return the final response
                    assistant_message = message.content or "I've completed the analysis."
                    self.conversation_history.append({"role": "assistant", "content": assistant_message})
                    return assistant_message
                    
            except Exception as e:
                return f"Error generating response: {str(e)}"
        
        # If we've exhausted iterations, return the last message
        return messages[-1].get("content", "I've completed the analysis using the available data.")
    
    def _execute_tool_call(self, tool_call) -> str:
        """
        Execute a tool call, routing to appropriate handler (MCP or Perplexity).
        
        Args:
            tool_call: Tool call object from OpenAI API
        
        Returns:
            JSON string of tool result
        """
        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)
        
        # Route to Perplexity research tool
        if function_name == "perplexity_research":
            if not self.perplexity_client:
                return json.dumps({
                    "error": "Perplexity API not configured",
                    "message": "Perplexity research is not available. Please set PERPLEXITY_API_KEY environment variable."
                }, indent=2)
            
            try:
                from src.perplexity_tools import execute_perplexity_research
                
                # Execute async Perplexity research using asyncio.run()
                result = asyncio.run(execute_perplexity_research(
                    self.perplexity_client,
                    query=function_args["query"],
                    focus=function_args.get("focus", "general")
                ))
                
                return json.dumps(result, indent=2)
            except Exception as e:
                return json.dumps({
                    "error": f"Perplexity research failed: {str(e)}",
                    "query": function_args.get("query", ""),
                    "status": "error"
                }, indent=2)
        
        # Route to MCP tools (existing logic)
        else:
            if not self.mcp_client:
                return json.dumps({
                    "error": "MCP client not available",
                    "message": f"Cannot execute tool {function_name}: MCP client is not initialized."
                }, indent=2)
            
            try:
                tool_result = execute_tool_by_name(
                    self.mcp_client,
                    function_name,
                    function_args
                )
                return json.dumps(tool_result, indent=2)
            except Exception as e:
                return json.dumps({
                    "error": f"Tool execution failed: {str(e)}",
                    "tool": function_name,
                    "status": "error"
                }, indent=2)
    
    def _get_fallback_response(self, user_message: str, system_instructions: str) -> str:
        """
        Fallback response using OpenAI API directly.
        
        Args:
            user_message: Current user message
            system_instructions: System instructions
        
        Returns:
            Agent's response
        """
        # Build messages for API call
        messages = [
            {"role": "system", "content": system_instructions},
        ]
        
        # Add conversation history (last few messages for context)
        for msg in self.conversation_history[-10:]:  # Keep last 10 messages
            if msg["role"] != "system":
                messages.append(msg)
        
        # Add current user message if not already in history
        if not any(msg.get("content") == user_message for msg in messages):
            messages.append({"role": "user", "content": user_message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
            )
            
            assistant_message = response.choices[0].message.content
            self.conversation_history.append({"role": "assistant", "content": assistant_message})
            
            return assistant_message
            
        except Exception as e:
            return f"Error generating response: {str(e)}"
    
    def reset_conversation(self):
        """Reset the conversation history."""
        self.conversation_history = []


def create_agent(api_key: Optional[str] = None) -> StockResearchAgent:
    """
    Create a new stock research agent instance.
    
    Args:
        api_key: OpenAI API key (optional, will use env var if not provided)
    
    Returns:
        StockResearchAgent instance
    """
    return StockResearchAgent(api_key=api_key)

