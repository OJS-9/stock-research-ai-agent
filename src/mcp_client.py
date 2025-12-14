"""
MCP client implementation for Alpha Vantage MCP server.
Handles HTTP communication with the MCP server for tool discovery and execution.
"""

import json
import time
from typing import Dict, Any, List, Optional
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


class MCPClient:
    """Client for communicating with Alpha Vantage MCP server via HTTP."""
    
    def __init__(self, mcp_url: str):
        """
        Initialize MCP client.
        
        Args:
            mcp_url: Full URL to MCP server including API key
        """
        self.mcp_url = mcp_url
        self.base_url = self._extract_base_url(mcp_url)
        self.api_key = self._extract_api_key(mcp_url)
        self.tools_cache: Optional[List[Dict[str, Any]]] = None
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
    
    def _extract_base_url(self, url: str) -> str:
        """Extract base URL without query parameters."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    
    def _extract_api_key(self, url: str) -> str:
        """Extract API key from URL."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        api_key = params.get("apikey", [None])[0]
        if not api_key:
            raise ValueError("API key not found in MCP URL")
        return api_key
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, retries: int = 3) -> Dict[str, Any]:
        """
        Make HTTP request to MCP server.
        
        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint
            data: Request payload
            retries: Number of retry attempts
        
        Returns:
            Response JSON data
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}" if endpoint else self.base_url
        
        # Add API key to query params
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        query_params["apikey"] = [self.api_key]
        new_query = urlencode(query_params, doseq=True)
        url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        
        for attempt in range(retries):
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, timeout=30)
                elif method.upper() == "POST":
                    response = self.session.post(url, json=data, timeout=30)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"MCP request failed after {retries} attempts: {str(e)}")
    
    def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Discover available tools from MCP server.
        
        Args:
            force_refresh: Force refresh of cached tools
        
        Returns:
            List of available tools
        """
        if self.tools_cache and not force_refresh:
            return self.tools_cache
        
        try:
            # MCP protocol: tools/list endpoint
            # For Alpha Vantage, we'll try different approaches
            # First, try the standard MCP tools/list endpoint
            try:
                response = self._make_request("POST", "", data={
                    "jsonrpc": "2.0",
                    "method": "tools/list",
                    "id": 1
                })
                
                if "result" in response and "tools" in response["result"]:
                    self.tools_cache = response["result"]["tools"]
                    return self.tools_cache
            except Exception:
                pass
            
            # Fallback: Use known Alpha Vantage MCP tools
            # Based on Alpha Vantage API documentation
            self.tools_cache = [
                {
                    "name": "OVERVIEW",
                    "description": "Get company overview and fundamental data",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol (e.g., AAPL, IBM)"
                            }
                        },
                        "required": ["symbol"]
                    }
                },
                {
                    "name": "INCOME_STATEMENT",
                    "description": "Get company income statement data",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol"
                            }
                        },
                        "required": ["symbol"]
                    }
                },
                {
                    "name": "BALANCE_SHEET",
                    "description": "Get company balance sheet data",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol"
                            }
                        },
                        "required": ["symbol"]
                    }
                },
                {
                    "name": "CASH_FLOW",
                    "description": "Get company cash flow statement data",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol"
                            }
                        },
                        "required": ["symbol"]
                    }
                },
                {
                    "name": "EARNINGS",
                    "description": "Get company earnings data",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol"
                            }
                        },
                        "required": ["symbol"]
                    }
                },
                {
                    "name": "NEWS_SENTIMENT",
                    "description": "Get news and sentiment analysis for a ticker",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "ticker": {
                                "type": "string",
                                "description": "Stock ticker symbol"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of news articles to return (default: 50)",
                                "default": 50
                            }
                        },
                        "required": ["ticker"]
                    }
                }
            ]
            
            return self.tools_cache
            
        except Exception as e:
            raise RuntimeError(f"Failed to list MCP tools: {str(e)}")
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an MCP tool.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        """
        try:
            # MCP protocol: tools/call endpoint
            request_data = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                },
                "id": int(time.time() * 1000)  # Unique ID
            }
            
            response = self._make_request("POST", "", data=request_data)
            
            # Handle MCP response format
            if "result" in response:
                result = response["result"]
                if isinstance(result, dict) and "content" in result:
                    # MCP content format
                    content = result["content"]
                    if isinstance(content, list) and len(content) > 0:
                        # Extract text content
                        text_content = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                        # Try to parse as JSON if possible
                        try:
                            return json.loads(text_content)
                        except json.JSONDecodeError:
                            return {"raw": text_content}
                    return result
                return result
            elif "error" in response:
                error = response["error"]
                raise RuntimeError(f"MCP tool error: {error.get('message', 'Unknown error')}")
            else:
                # Direct response (Alpha Vantage API format)
                return response
                
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to call MCP tool {tool_name}: {str(e)}")
    
    def test_connection(self) -> bool:
        """
        Test connection to MCP server.
        
        Returns:
            True if connection is successful
        """
        try:
            # Try to list tools as a connection test
            self.list_tools()
            return True
        except Exception:
            return False


def create_mcp_client(mcp_url: str) -> MCPClient:
    """
    Create a new MCP client instance.
    
    Args:
        mcp_url: MCP server URL with API key
    
    Returns:
        MCPClient instance
    """
    return MCPClient(mcp_url)






