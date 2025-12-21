"""
Perplexity Sonar API client for real-time web research.
"""

import os
import asyncio
from typing import Optional

import httpx
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Default timeout (seconds) for Perplexity HTTP requests
PERPLEXITY_TIMEOUT_SECONDS = float(os.getenv("PERPLEXITY_TIMEOUT_SECONDS", "10.0"))


class PerplexityClient:
    """
    Client for Perplexity's Sonar API using AsyncOpenAI.
    
    Provides access to Perplexity's real-time web research capabilities
    through the Sonar models (sonar, sonar::M, sonar::L).
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "sonar"):
        """
        Initialize Perplexity client.
        
        Args:
            api_key: Perplexity API key. If None, reads from PERPLEXITY_API_KEY env var.
            model: Sonar model to use. Options: "sonar", "sonar::M", "sonar::L"
        
        Raises:
            ValueError: If API key is not provided and not found in environment.
        """
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        self.model = model or os.getenv("PERPLEXITY_MODEL", "sonar")
        
        if not self.api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY is required. "
                "Set PERPLEXITY_API_KEY environment variable or pass api_key parameter."
            )
        
        # Initialize AsyncOpenAI client with Perplexity base URL and timeout
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://api.perplexity.ai",
            timeout=httpx.Timeout(PERPLEXITY_TIMEOUT_SECONDS),
        )
    
    async def research(
        self,
        query: str,
        system_message: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000
    ) -> str:
        """
        Perform research query using Perplexity Sonar API.
        
        Args:
            query: Research query or question to investigate
            system_message: Optional system message to guide the research
            temperature: Sampling temperature (default: 0.2 for factual research)
            max_tokens: Maximum tokens in response
        
        Returns:
            Research response content as string
        """
        messages = []
        
        if system_message:
            messages.append({"role": "system", "content": system_message})
        else:
            messages.append({
                "role": "system",
                "content": "You are a helpful research assistant that provides accurate, cited information."
            })
        
        messages.append({"role": "user", "content": query})
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content or ""
        except (APITimeoutError, APIConnectionError, asyncio.TimeoutError) as e:
            return (
                f"[Perplexity timeout] Research request exceeded "
                f"{PERPLEXITY_TIMEOUT_SECONDS:.0f}s limit: {e}"
            )
        except Exception as e:
            return f"[Perplexity error] Research request failed: {e}"
    
    
def create_perplexity_client(api_key: Optional[str] = None, model: Optional[str] = None) -> PerplexityClient:
    """
    Create a new Perplexity client instance.
    
    Args:
        api_key: Perplexity API key (optional, will use env var if not provided)
        model: Sonar model to use (optional, will use env var or default if not provided)
    
    Returns:
        PerplexityClient instance
    """
    return PerplexityClient(api_key=api_key, model=model or "sonar")
    
    