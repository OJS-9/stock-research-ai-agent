"""
Agents SDK tool wrappers for MCP and Perplexity tools.
Converts existing tool functions to Agents SDK Tool format.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional
from agents import Tool

from mcp_client import MCPClient
from mcp_tools import execute_tool_by_name, TOOL_NAME_MAPPING
from perplexity_client import PerplexityClient
from perplexity_tools import execute_perplexity_research


def create_mcp_tool_wrapper(mcp_client: MCPClient, tool_name: str, description: str, input_schema: Dict[str, Any]):
    """
    Create a wrapper function for an MCP tool that can be used with Agents SDK.
    
    Args:
        mcp_client: MCP client instance
        tool_name: Name of the MCP tool
        description: Tool description
        input_schema: Tool input schema
    
    Returns:
        Callable function for the tool
    """
    def tool_function(**kwargs) -> Dict[str, Any]:
        """
        Execute MCP tool with given arguments.
        
        Args:
            **kwargs: Tool arguments from input_schema
        
        Returns:
            Tool execution result
        """
        try:
            result = execute_tool_by_name(mcp_client, tool_name, kwargs)
            # Convert result to string if it's a dict (Agents SDK expects string return)
            if isinstance(result, dict):
                return json.dumps(result, indent=2)
            return str(result)
        except Exception as e:
            error_result = {
                "error": f"Tool execution failed: {str(e)}",
                "tool": tool_name,
                "status": "error"
            }
            return json.dumps(error_result, indent=2)
    
    return tool_function


def create_mcp_tools(mcp_client: MCPClient) -> List[Tool]:
    """
    Create Agents SDK Tool objects for all available MCP tools.
    
    Args:
        mcp_client: MCP client instance
    
    Returns:
        List of Tool objects
    """
    if not mcp_client:
        return []
    
    tools = mcp_client.list_tools()
    tool_objects = []
    
    for tool in tools:
        tool_name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {})
        
        # Create wrapper function
        tool_func = create_mcp_tool_wrapper(mcp_client, tool_name, description, input_schema)
        
        # Normalize tool name for Agents SDK (use lowercase with underscores)
        normalized_name = tool_name.lower().replace("-", "_")
        
        # Create Tool object
        tool_obj = Tool(
            name=normalized_name,
            description=description,
            func=tool_func,
            parameters=input_schema
        )
        
        tool_objects.append(tool_obj)
    
    return tool_objects


def create_perplexity_tool_wrapper(perplexity_client: PerplexityClient):
    """
    Create a wrapper function for Perplexity research tool.
    
    Args:
        perplexity_client: PerplexityClient instance
    
    Returns:
        Callable async function for the tool
    """
    async def perplexity_research(query: str, focus: str = "general") -> str:
        """
        Execute Perplexity research query.
        
        Args:
            query: Research query or question
            focus: Focus area (news, analysis, general, financial)
        
        Returns:
            JSON string with research results
        """
        try:
            result = await execute_perplexity_research(
                perplexity_client,
                query=query,
                focus=focus
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            error_result = {
                "error": f"Perplexity research failed: {str(e)}",
                "query": query,
                "status": "error"
            }
            return json.dumps(error_result, indent=2)
    
    return perplexity_research


def create_perplexity_tool(perplexity_client: PerplexityClient) -> Optional[Tool]:
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
    
    # Create Tool object
    tool_obj = Tool(
        name="perplexity_research",
        description=(
            "Perform real-time web research on a topic using Perplexity's Sonar API. "
            "Use this for finding recent news, market analysis, company developments, "
            "industry trends, and other information not available in structured financial data. "
            "Returns comprehensive, cited research results with sources. "
            "Use this tool when you need current information, news, expert opinions, "
            "or qualitative analysis that complements the structured financial data from Alpha Vantage tools."
        ),
        func=tool_func,
        parameters=parameters
    )
    
    return tool_obj


def create_all_tools(mcp_client: Optional[MCPClient], perplexity_client: Optional[PerplexityClient]) -> List[Tool]:
    """
    Create all available tools for Agents SDK.
    
    Args:
        mcp_client: MCP client instance (optional)
        perplexity_client: Perplexity client instance (optional)
    
    Returns:
        List of all Tool objects
    """
    all_tools = []
    
    # Add MCP tools
    if mcp_client:
        mcp_tools = create_mcp_tools(mcp_client)
        all_tools.extend(mcp_tools)
    
    # Add Perplexity tool
    if perplexity_client:
        perplexity_tool = create_perplexity_tool(perplexity_client)
        if perplexity_tool:
            all_tools.append(perplexity_tool)
    
    return all_tools




