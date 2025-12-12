"""
MCP tool wrappers for Alpha Vantage tools.
Provides Python functions that wrap MCP tool calls and OpenAI function definitions.
"""

from typing import Dict, Any, List, Optional
from src.mcp_client import MCPClient


def get_openai_function_definitions(mcp_client: MCPClient) -> List[Dict[str, Any]]:
    """
    Get OpenAI function definitions for all available MCP tools.
    
    Args:
        mcp_client: MCP client instance
    
    Returns:
        List of OpenAI function definitions
    """
    tools = mcp_client.list_tools()
    function_definitions = []
    
    for tool in tools:
        tool_name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {})
        
        # Convert MCP input schema to OpenAI function format
        function_def = {
            "type": "function",
            "function": {
                "name": tool_name.lower().replace("_", "_"),  # Keep original name
                "description": description,
                "parameters": input_schema
            }
        }
        
        function_definitions.append(function_def)
    
    return function_definitions


def call_mcp_tool(mcp_client: MCPClient, tool_name: str, **kwargs) -> Dict[str, Any]:
    """
    Call an MCP tool with given arguments.
    
    Args:
        mcp_client: MCP client instance
        tool_name: Name of the tool to call
        **kwargs: Tool arguments
    
    Returns:
        Tool execution result
    """
    return mcp_client.call_tool(tool_name, kwargs)


# Specific tool wrapper functions for common operations

def get_company_overview(mcp_client: MCPClient, symbol: str) -> Dict[str, Any]:
    """
    Get company overview and fundamental data.
    
    Args:
        mcp_client: MCP client instance
        symbol: Stock ticker symbol
    
    Returns:
        Company overview data
    """
    result = mcp_client.call_tool("OVERVIEW", {"symbol": symbol.upper()})
    return result


def get_income_statement(mcp_client: MCPClient, symbol: str) -> Dict[str, Any]:
    """
    Get company income statement data.
    
    Args:
        mcp_client: MCP client instance
        symbol: Stock ticker symbol
    
    Returns:
        Income statement data
    """
    result = mcp_client.call_tool("INCOME_STATEMENT", {"symbol": symbol.upper()})
    return result


def get_balance_sheet(mcp_client: MCPClient, symbol: str) -> Dict[str, Any]:
    """
    Get company balance sheet data.
    
    Args:
        mcp_client: MCP client instance
        symbol: Stock ticker symbol
    
    Returns:
        Balance sheet data
    """
    result = mcp_client.call_tool("BALANCE_SHEET", {"symbol": symbol.upper()})
    return result


def get_cash_flow(mcp_client: MCPClient, symbol: str) -> Dict[str, Any]:
    """
    Get company cash flow statement data.
    
    Args:
        mcp_client: MCP client instance
        symbol: Stock ticker symbol
    
    Returns:
        Cash flow statement data
    """
    result = mcp_client.call_tool("CASH_FLOW", {"symbol": symbol.upper()})
    return result


def get_earnings(mcp_client: MCPClient, symbol: str) -> Dict[str, Any]:
    """
    Get company earnings data.
    
    Args:
        mcp_client: MCP client instance
        symbol: Stock ticker symbol
    
    Returns:
        Earnings data
    """
    result = mcp_client.call_tool("EARNINGS", {"symbol": symbol.upper()})
    return result


def get_news_sentiment(mcp_client: MCPClient, ticker: str, limit: int = 50) -> Dict[str, Any]:
    """
    Get news and sentiment analysis for a ticker.
    
    Args:
        mcp_client: MCP client instance
        ticker: Stock ticker symbol
        limit: Number of news articles to return (default: 50)
    
    Returns:
        News and sentiment data
    """
    result = mcp_client.call_tool("NEWS_SENTIMENT", {"ticker": ticker.upper(), "limit": limit})
    return result


# Tool name mapping for OpenAI function calling
TOOL_NAME_MAPPING = {
    "overview": "OVERVIEW",
    "income_statement": "INCOME_STATEMENT",
    "balance_sheet": "BALANCE_SHEET",
    "cash_flow": "CASH_FLOW",
    "earnings": "EARNINGS",
    "news_sentiment": "NEWS_SENTIMENT",
}


def execute_tool_by_name(mcp_client: MCPClient, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute an MCP tool by OpenAI function name.
    
    Args:
        mcp_client: MCP client instance
        function_name: OpenAI function name (will be mapped to MCP tool name)
        arguments: Function arguments
    
    Returns:
        Tool execution result
    """
    # Map OpenAI function name to MCP tool name
    mcp_tool_name = TOOL_NAME_MAPPING.get(function_name.lower(), function_name.upper())
    
    # Handle special cases
    if mcp_tool_name == "OVERVIEW" and "symbol" in arguments:
        return get_company_overview(mcp_client, arguments["symbol"])
    elif mcp_tool_name == "INCOME_STATEMENT" and "symbol" in arguments:
        return get_income_statement(mcp_client, arguments["symbol"])
    elif mcp_tool_name == "BALANCE_SHEET" and "symbol" in arguments:
        return get_balance_sheet(mcp_client, arguments["symbol"])
    elif mcp_tool_name == "CASH_FLOW" and "symbol" in arguments:
        return get_cash_flow(mcp_client, arguments["symbol"])
    elif mcp_tool_name == "EARNINGS" and "symbol" in arguments:
        return get_earnings(mcp_client, arguments["symbol"])
    elif mcp_tool_name == "NEWS_SENTIMENT" and "ticker" in arguments:
        limit = arguments.get("limit", 50)
        return get_news_sentiment(mcp_client, arguments["ticker"], limit)
    else:
        # Generic tool call
        return mcp_client.call_tool(mcp_tool_name, arguments)

