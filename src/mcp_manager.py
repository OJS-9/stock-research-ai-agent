"""
MCP server connection and management for Alpha Vantage.
"""

import json
import os
from typing import Optional, Dict, Any, List
from pathlib import Path

from src.mcp_client import MCPClient, create_mcp_client


class MCPManager:
    """Manages connection to Alpha Vantage MCP server."""
    
    def __init__(self, mcp_config_path: Optional[str] = None):
        """
        Initialize MCP manager.
        
        Args:
            mcp_config_path: Path to mcp.json file. If None, looks for mcp.json in project root.
        """
        if mcp_config_path is None:
            project_root = Path(__file__).parent.parent
            mcp_config_path = project_root / "mcp.json"
        
        self.mcp_config_path = Path(mcp_config_path)
        self.config = self._load_config()
        self.mcp_url = self._get_mcp_url()
        self.mcp_client: Optional[MCPClient] = None
    
    def _load_config(self) -> Dict[str, Any]:
        """Load MCP configuration from JSON file."""
        try:
            if not self.mcp_config_path.exists():
                raise FileNotFoundError(
                    f"MCP config file not found: {self.mcp_config_path}. "
                    f"Please copy mcp.json.example to mcp.json and configure it."
                )
            
            with open(self.mcp_config_path, 'r') as f:
                config = json.load(f)
            
            return config
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in MCP config: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load MCP config: {e}")
    
    def _get_mcp_url(self) -> str:
        """Extract MCP server URL from configuration."""
        try:
            servers = self.config.get("servers", {})
            alphavantage_config = servers.get("alphavantage", {})
            
            if not alphavantage_config:
                raise ValueError("Alpha Vantage server configuration not found in mcp.json")
            
            server_type = alphavantage_config.get("type", "http")
            if server_type != "http":
                raise ValueError(f"Only HTTP mode is currently supported, got: {server_type}")
            
            url = alphavantage_config.get("url", "")
            if not url:
                raise ValueError("MCP server URL not found in configuration")
            
            # Check if API key is in URL or needs to be added
            if "YOUR_API_KEY" in url or "apikey=" not in url:
                # Try to get API key from environment
                api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
                if api_key:
                    if "?" in url:
                        url = f"{url}&apikey={api_key}"
                    else:
                        url = f"{url}?apikey={api_key}"
                else:
                    raise ValueError(
                        "Alpha Vantage API key not found. "
                        "Either include it in mcp.json URL or set ALPHA_VANTAGE_API_KEY environment variable."
                    )
            
            return url
        except Exception as e:
            raise RuntimeError(f"Failed to get MCP URL: {e}")
    
    def get_mcp_client(self) -> MCPClient:
        """
        Get or create MCP client instance.
        
        Returns:
            MCPClient instance
        """
        if self.mcp_client is None:
            self.mcp_client = create_mcp_client(self.mcp_url)
        return self.mcp_client
    
    def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Discover available tools from MCP server.
        
        Args:
            force_refresh: Force refresh of cached tools
        
        Returns:
            List of available tools
        """
        client = self.get_mcp_client()
        return client.list_tools(force_refresh=force_refresh)
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an MCP tool.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        """
        client = self.get_mcp_client()
        return client.call_tool(tool_name, arguments)
    
    def get_mcp_config(self) -> Dict[str, Any]:
        """
        Get MCP configuration for OpenAI Agents SDK.
        
        Returns:
            Dictionary with MCP server configuration
        """
        return {
            "type": "http",
            "url": self.mcp_url,
            "label": "Alpha Vantage MCP Server",
            "description": "Financial market data and technical indicators from Alpha Vantage"
        }
    
    def test_connection(self) -> bool:
        """
        Test connection to MCP server.
        
        Returns:
            True if connection is successful
        """
        try:
            client = self.get_mcp_client()
            return client.test_connection()
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
    
    # Convenience methods for common operations
    def get_company_overview(self, symbol: str) -> Dict[str, Any]:
        """Get company overview data."""
        return self.call_tool("OVERVIEW", {"symbol": symbol.upper()})
    
    def get_income_statement(self, symbol: str) -> Dict[str, Any]:
        """Get income statement data."""
        return self.call_tool("INCOME_STATEMENT", {"symbol": symbol.upper()})
    
    def get_balance_sheet(self, symbol: str) -> Dict[str, Any]:
        """Get balance sheet data."""
        return self.call_tool("BALANCE_SHEET", {"symbol": symbol.upper()})
    
    def get_earnings(self, symbol: str) -> Dict[str, Any]:
        """Get earnings data."""
        return self.call_tool("EARNINGS", {"symbol": symbol.upper()})
    
    def get_news_sentiment(self, ticker: str, limit: int = 50) -> Dict[str, Any]:
        """Get news and sentiment data."""
        return self.call_tool("NEWS_SENTIMENT", {"ticker": ticker.upper(), "limit": limit})


def get_mcp_manager() -> MCPManager:
    """
    Get a configured MCP manager instance.
    
    Returns:
        Configured MCPManager instance
    """
    return MCPManager()

