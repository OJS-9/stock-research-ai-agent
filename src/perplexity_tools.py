"""
Perplexity research tools for OpenAI function calling.
Provides function definitions and execution handlers for Perplexity Sonar API.
"""

from typing import Dict, Any, Optional
from perplexity_client import PerplexityClient


def get_perplexity_research_function() -> Dict[str, Any]:
    """
    Get OpenAI function definition for Perplexity research tool.
    
    Returns:
        OpenAI function definition dictionary
    """
    return {
        "type": "function",
        "function": {
            "name": "perplexity_research",
            "description": (
                "Perform real-time web research on a topic using Perplexity's Sonar API. "
                "Use this for finding recent news, market analysis, company developments, "
                "industry trends, and other information not available in structured financial data. "
                "Returns comprehensive, cited research results with sources. "
                "Use this tool when you need current information, news, expert opinions, "
                "or qualitative analysis that complements the structured financial data from Alpha Vantage tools."
            ),
            "parameters": {
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
        }
    }


def _format_query(query: str, focus: str = "general") -> str:
    """
    Format research query based on focus type.
    
    Args:
        query: Original query
        focus: Focus type (news, analysis, general, financial)
    
    Returns:
        Formatted query string
    """
    focus_prefixes = {
        "news": "Recent news and events: ",
        "analysis": "Expert analysis and opinions: ",
        "financial": "Financial market context: ",
        "general": ""
    }
    
    prefix = focus_prefixes.get(focus, "")
    return f"{prefix}{query}" if prefix else query


async def execute_perplexity_research(
    perplexity_client: PerplexityClient,
    query: str,
    focus: str = "general"
) -> Dict[str, Any]:
    """
    Execute Perplexity research query.
    
    Args:
        perplexity_client: PerplexityClient instance
        query: Research query or question
        focus: Focus area for research (news, analysis, general, financial)
    
    Returns:
        Dictionary with research results:
            - query: Original query
            - research: Research response content
            - focus: Focus type used
    """
    # Format query based on focus
    formatted_query = _format_query(query, focus)
    
    # Create system message based on focus
    system_messages = {
        "news": "You are a financial news research assistant. Provide recent news, events, and developments with sources.",
        "analysis": "You are a financial analysis assistant. Provide expert opinions, market analysis, and insights with sources.",
        "financial": "You are a financial market research assistant. Provide financial context, market trends, and economic factors with sources.",
        "general": "You are a helpful research assistant that provides accurate, cited information."
    }
    
    system_message = system_messages.get(focus, system_messages["general"])
    
    try:
        # Call Perplexity API
        research_content = await perplexity_client.research(
            query=formatted_query,
            system_message=system_message,
            temperature=0.2,  # Lower temperature for factual research
            max_tokens=2000
        )
        
        return {
            "query": query,
            "research": research_content,
            "focus": focus,
            "status": "success"
        }
    except Exception as e:
        return {
            "query": query,
            "research": f"Error performing research: {str(e)}",
            "focus": focus,
            "status": "error",
            "error": str(e)
        }

