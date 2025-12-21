"""
Agents SDK tool wrappers for MCP and Perplexity tools.
Converts existing tool functions to Agents SDK Tool format.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional
from agents import Tool
from agents.tool import FunctionTool  # Use concrete FunctionTool class instead of Tool Union

from mcp_client import MCPClient
from mcp_tools import execute_tool_by_name, TOOL_NAME_MAPPING
from perplexity_client import PerplexityClient
from perplexity_tools import execute_perplexity_research

# Limits for large tool outputs to keep token usage under control
MAX_SERIES_ITEMS = 5
MAX_NEWS_ITEMS = 5
MAX_RESEARCH_ITEMS = 3

def create_mcp_tool_wrapper(mcp_client: MCPClient, mcp_tool_name: str, description: str, input_schema: Dict[str, Any]):
    """
    Create a wrapper function for an MCP tool that can be used with Agents SDK.
    
    Args:
        mcp_client: MCP client instance
        mcp_tool_name: Original MCP tool name (e.g., "TIME_SERIES_INTRADAY")
        description: Tool description
        input_schema: Tool input schema
    
    Returns:
        Callable function for the tool
    """
    async def tool_function(context, tool_input: Dict[str, Any]) -> str:
        """
        Execute MCP tool with given arguments.
        
        Args:
            context: Tool context (provided by SDK)
            tool_input: Tool input arguments dict from input_schema
        
        Returns:
            Tool execution result as JSON string
        """        
        try:
            # Handle tool_input - SDK passes it as a JSON string, not a dict
            if isinstance(tool_input, str):
                # Parse JSON string
                try:
                    tool_args = json.loads(tool_input)
                except (json.JSONDecodeError, ValueError) as json_err:
                    # If not valid JSON, return error
                    error_result = {
                        "error": f"Invalid JSON in tool_input: {str(json_err)}",
                        "tool": mcp_tool_name,
                        "status": "error"
                    }
                    return json.dumps(error_result, indent=2, default=str)
            elif isinstance(tool_input, dict):
                tool_args = tool_input
            else:
                # Unexpected type
                error_result = {
                    "error": f"Unexpected tool_input type: {type(tool_input).__name__}",
                    "tool": mcp_tool_name,
                    "status": "error"
                }
                return json.dumps(error_result, indent=2, default=str)
            
            # Use the original MCP tool name (not normalized) when calling
            result = execute_tool_by_name(mcp_client, mcp_tool_name, tool_args)
            
            # Convert result to string if it's a dict (Agents SDK expects string return)
            if isinstance(result, dict):
                # Truncate large series-like lists to reduce prompt size
                for key in (
                    "annualReports",
                    "quarterlyReports",
                    "monthlyReports",
                    "reports",
                    "items",
                    "data",
                ):
                    if key in result and isinstance(result[key], list):
                        if len(result[key]) > MAX_SERIES_ITEMS:
                            result[key] = result[key][:MAX_SERIES_ITEMS]
                if "feed" in result and isinstance(result["feed"], list):
                    if len(result["feed"]) > MAX_NEWS_ITEMS:
                        result["feed"] = result["feed"][:MAX_NEWS_ITEMS]
                return json.dumps(result, indent=2)
            return str(result)
        except Exception as e:
            error_result = {
                "error": f"Tool execution failed: {str(e)}",
                "tool": mcp_tool_name,
                "status": "error"
            }
            return json.dumps(error_result, indent=2)
    
    return tool_function

def create_mcp_tools(mcp_client: MCPClient) -> List[FunctionTool]:
    """
    Create Agents SDK Tool objects for MCP tools listed in research_prompt.py.
    
    Args:
        mcp_client: MCP client instance
    
    Returns:
        List of Tool objects for the 6 essential tools from research_prompt.py:
        - OVERVIEW (company overview and fundamentals)
        - INCOME_STATEMENT (income statement data)
        - BALANCE_SHEET (balance sheet data)
        - CASH_FLOW (cash flow statement data)
        - EARNINGS (earnings data)
        - NEWS_SENTIMENT (news articles and sentiment analysis)
    """
    if not mcp_client:
        return []
    
    all_tools = mcp_client.list_tools()
    
    # Essential tools from research_prompt.py (lines 98-120)
    # Only the 6 tools explicitly documented in the research instructions
    essential_tool_names = {
        "OVERVIEW",           # Line 98: company overview and fundamentals
        "INCOME_STATEMENT",   # Line 102: income statement data
        "BALANCE_SHEET",      # Line 106: balance sheet data
        "CASH_FLOW",          # Line 110: cash flow statement data
        "EARNINGS",           # Line 114: earnings data
        "NEWS_SENTIMENT",     # Line 118: news articles and sentiment
    }
    
    tools = [tool for tool in all_tools if tool.get("name", "") in essential_tool_names]
    
    tool_objects = []
    
    for idx, tool in enumerate(tools):
        mcp_tool_name = tool.get("name", "")  # Original MCP tool name (e.g., "TIME_SERIES_INTRADAY")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {})
        
        # Create wrapper function - pass original MCP tool name
        tool_func = create_mcp_tool_wrapper(mcp_client, mcp_tool_name, description, input_schema)
        
        # Normalize tool name for Agents SDK (use lowercase with underscores)
        normalized_name = mcp_tool_name.lower().replace("-", "_")
        
        try:
            # Create FunctionTool object (Tool is a Union type alias, not a class)            
            # Try to inspect FunctionTool signature to find correct parameter names
            import inspect
            try:
                sig = inspect.signature(FunctionTool.__init__)
                func_params = [p for p in sig.parameters.keys() if p != 'self']
            except Exception as e:
                pass            
            # Create FunctionTool - use correct parameter names from inspection
            # params_json_schema instead of parameters, on_invoke_tool instead of invoke/func/function
            tool_obj = FunctionTool(
                name=normalized_name,
                description=description,
                params_json_schema=input_schema,  # Use params_json_schema, not parameters
                on_invoke_tool=tool_func  # Use on_invoke_tool - must be async function
            )
            
            tool_objects.append(tool_obj)
        except Exception as e:
            import traceback
            raise  # Re-raise to see full traceback    
    return tool_objects

def create_perplexity_tool_wrapper(perplexity_client: PerplexityClient):
    """
    Create a wrapper function for Perplexity research tool.
    
    Args:
        perplexity_client: PerplexityClient instance
    
    Returns:
        Callable async function for the tool
    """
    async def perplexity_research(context, tool_input: Dict[str, Any]) -> str:
        """
        Execute Perplexity research query.
        
        Args:
            context: Tool context (provided by SDK, not used - ignore this parameter)
            tool_input: Tool input arguments (can be dict or JSON string)
        
        Returns:
            JSON string with research results
        """
        # Immediately discard context to prevent any serialization issues
        _ = context  # Explicitly mark as unused        
        # Safely extract query and focus parameters
        query = ""
        focus = "general"
        
        try:
            # Parse tool_input - SDK passes it as a JSON string, not a dict
            parsed_input = None
            if isinstance(tool_input, str):
                # Parse JSON string
                try:
                    parsed_input = json.loads(tool_input)
                except (json.JSONDecodeError, ValueError) as json_err:
                    # If not valid JSON, return error
                    return json.dumps({
                        "error": f"Invalid JSON in tool_input: {str(json_err)}",
                        "status": "error"
                    }, indent=2, default=str)
            elif isinstance(tool_input, dict):
                parsed_input = tool_input
            else:
                # Unexpected type
                return json.dumps({
                    "error": f"Unexpected tool_input type: {type(tool_input).__name__}",
                    "status": "error"
                }, indent=2, default=str)
            
            # Now parsed_input should be a dict
            if isinstance(parsed_input, dict):
                # Extract query and focus
                query = str(parsed_input.get("query", "")) if parsed_input.get("query") else ""
                focus = str(parsed_input.get("focus", "general")) if parsed_input.get("focus") else "general"
            else:
                # Should not happen, but handle gracefully
                return json.dumps({
                    "error": f"Parsed input is not a dict: {type(parsed_input).__name__}",
                    "status": "error"
                }, indent=2, default=str)
            
            if not query:
                return json.dumps({
                    "error": "Query parameter is required",
                    "status": "error"
                }, indent=2)
            
            # Call Perplexity research
            result = await execute_perplexity_research(
                perplexity_client,
                query=query,
                focus=focus
            )
            # Convert result to JSON string - result should already be a dict
            if isinstance(result, dict):
                # Trim large result lists to keep context compact
                for key in ("results", "answers", "citations", "items"):
                    if key in result and isinstance(result[key], list):
                        if len(result[key]) > MAX_RESEARCH_ITEMS:
                            result[key] = result[key][:MAX_RESEARCH_ITEMS]
                return json.dumps(result, indent=2, default=str)  # Use default=str for non-serializable values
            return json.dumps({"research": str(result), "status": "success"}, indent=2)
            
        except Exception as e:            
            # Return error as JSON string with safe serialization
            error_result = {
                "error": f"Perplexity research failed: {str(e)}",
                "status": "error"
            }
            try:
                return json.dumps(error_result, indent=2, default=str)
            except Exception:
                # Ultimate fallback - just return error message as string
                return f'{{"error": "Perplexity research failed: {str(e)}", "status": "error"}}'
    
    return perplexity_research

def create_perplexity_tool(perplexity_client: PerplexityClient) -> Optional[FunctionTool]:
    """
    Create Agents SDK Tool object for Perplexity research.
    
    Args:
        perplexity_client: PerplexityClient instance
    
    Returns:
        Tool object or None if client is not available
    """    
    if not perplexity_client:
        return None
    
    # Create wrapper function
    tool_func = create_perplexity_tool_wrapper(perplexity_client)    
    # Tool schema
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Research query or question to investigate. "
                    "Be specific and include context (e.g., company name, ticker symbol, time period). "
                    "Examples: 'Recent Apple Inc news and market sentiment', "
                    "'TSLA stock analysis and analyst opinions', "
                    "'Technology sector trends affecting semiconductor stocks'"
                )
            },
            "focus": {
                "type": "string",
                "enum": ["news", "analysis", "general", "financial"],
                "description": (
                    "Focus area for the research. "
                    "'news' for recent news and events, "
                    "'analysis' for expert analysis and opinions, "
                    "'financial' for financial market context, "
                    "'general' for broad research (default)."
                ),
                "default": "general"
            }
        },
        "required": ["query"]
    }    
    try:
        # Create FunctionTool object (Tool is a Union type alias, not a class)
        tool_obj = FunctionTool(
            name="perplexity_research",
            description=(
                "Perform real-time web research on a topic using Perplexity's Sonar API. "
                "Use this for finding recent news, market analysis, company developments, "
                "industry trends, and other information not available in structured financial data. "
                "Returns comprehensive, cited research results with sources. "
                "Use this tool when you need current information, news, expert opinions, "
                "or qualitative analysis that complements the structured financial data from Alpha Vantage tools."
            ),
            params_json_schema=parameters,  # Use params_json_schema, not parameters
            on_invoke_tool=tool_func  # Use on_invoke_tool, not invoke/func/function
        )        
        return tool_obj
    except Exception as e:        raise

def create_all_tools(mcp_client: Optional[MCPClient], perplexity_client: Optional[PerplexityClient]) -> List[FunctionTool]:
    """
    Create all available tools for Agents SDK.
    
    Args:
        mcp_client: MCP client instance (optional)
        perplexity_client: Perplexity client instance (optional)
    
    Returns:
        List of all Tool objects - includes 6 essential MCP tools from research_prompt.py
        plus Perplexity research tool (if client is available)
    
    Note:
        MCP tools are limited to the 6 tools documented in research_prompt.py:
        OVERVIEW, INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS, NEWS_SENTIMENT
    """    
    all_tools = []
    
    # Add MCP tools
    if mcp_client:
        try:
            mcp_tools = create_mcp_tools(mcp_client)
            all_tools.extend(mcp_tools)
        except Exception as e:
            raise
    
    # Add Perplexity tool
    if perplexity_client:
        try:
            perplexity_tool = create_perplexity_tool(perplexity_client)
            if perplexity_tool:
                all_tools.append(perplexity_tool)
        except Exception as e:
            raise    
    return all_tools

