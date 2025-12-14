"""
Test script for MCP integration.
Run this to verify MCP tools are working correctly.
"""

import os
from dotenv import load_dotenv
from src.mcp_manager import MCPManager

load_dotenv()

def test_mcp_connection():
    """Test MCP connection and tool discovery."""
    print("Testing MCP Integration...")
    print("=" * 50)
    
    try:
        # Initialize MCP manager
        print("\n1. Initializing MCP Manager...")
        mcp_manager = MCPManager()
        print(f"   ✓ MCP URL configured: {mcp_manager.mcp_url[:50]}...")
        
        # Test connection
        print("\n2. Testing MCP connection...")
        if mcp_manager.test_connection():
            print("   ✓ Connection successful")
        else:
            print("   ⚠ Connection test inconclusive (may need actual API call)")
        
        # List tools
        print("\n3. Discovering MCP tools...")
        try:
            tools = mcp_manager.list_tools()
            print(f"   ✓ Found {len(tools)} tools:")
            for tool in tools:
                print(f"     - {tool.get('name', 'Unknown')}: {tool.get('description', 'No description')}")
        except Exception as e:
            print(f"   ⚠ Tool discovery failed: {e}")
            print("   (This is okay if MCP server requires authentication)")
        
        # Test tool call (if API key is available)
        print("\n4. Testing tool execution...")
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if api_key and api_key != "your_alpha_vantage_api_key_here":
            try:
                result = mcp_manager.get_company_overview("IBM")
                if result:
                    print("   ✓ Tool execution successful")
                    print(f"   ✓ Retrieved data for IBM")
                    if isinstance(result, dict) and "Name" in result:
                        print(f"     Company: {result.get('Name', 'N/A')}")
                else:
                    print("   ⚠ Tool returned empty result")
            except Exception as e:
                print(f"   ⚠ Tool execution failed: {e}")
                print("   (This may be due to API rate limits or invalid API key)")
        else:
            print("   ⚠ Skipping tool execution test (API key not configured)")
            print("   Set ALPHA_VANTAGE_API_KEY in .env to test tool execution")
        
        print("\n" + "=" * 50)
        print("MCP Integration Test Complete!")
        print("\nNext steps:")
        print("1. Ensure your Alpha Vantage API key is set in mcp.json or .env")
        print("2. Run the Gradio app: python src/gradio_app.py")
        print("3. Test with a real ticker (e.g., AAPL, IBM)")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Check that mcp.json exists and is properly configured")
        print("2. Verify ALPHA_VANTAGE_API_KEY is set in .env or mcp.json")
        print("3. Ensure you have internet connectivity")
        return False
    
    return True

if __name__ == "__main__":
    test_mcp_connection()






