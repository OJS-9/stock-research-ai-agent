# AI Agent with Alpha Vantage MCP - Cursor Rules

**Rule Type:** Always  
**AI Model Focus:** Code generation, architecture decisions, and real-time financial data integration  
**Framework:** Agents SDK + Alpha Vantage MCP

---

## 🎯 Core Philosophy: Vibe Coding

We're building **smooth, flow-state development** where the AI agent seamlessly orchestrates financial data queries through Alpha Vantage MCP. Prioritize:

- **Clarity over cleverness** – readable, maintainable agent logic
- **Real data, real fast** – leverage MCP tools for instant market data without API friction
- **Async-first thinking** – agents operate on non-blocking patterns
- **Type safety** – catch errors before runtime in agent decisions
- **Minimal boilerplate** – let the SDK handle complexity, focus on agent behavior

---

## 📋 Project Structure & Essentials

### Required Setup Pattern

```python
# project root
from agents_sdk import Agent, Tool
from alpha_vantage_mcp import AlphaVantageMCP

# Initialize MCP client for financial data
mcp_client = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))

# Agent setup
agent = Agent(
    name="market_analyst",
    description="Real-time financial analysis agent",
    tools=[
        # MCP tools auto-registered
        mcp_client.get_stock_quote(),
        mcp_client.get_company_info(),
        mcp_client.get_historical_data(),
        mcp_client.get_crypto_rates(),
    ]
)
```

### Key Dependencies (Always Include)

```toml
[dependencies]
agents-sdk = ">=0.2.0"
alpha-vantage-mcp = ">=1.0.0"  # Specific version critical
httpx = "^0.24.0"  # For async requests
pydantic = "^2.0"  # For type validation
python-dotenv = "^1.0.0"  # API key management
```

### Directory Layout

```
project/
├── .cursorrules (this file)
├── .env (ALPHAVANTAGE_API_KEY, other secrets)
├── agents/
│   ├── __init__.py
│   ├── base_agent.py (Agent initialization patterns)
│   ├── market_agent.py (Stock/crypto analysis)
│   └── portfolio_agent.py (Holdings analysis)
├── tools/
│   ├── mcp_wrapper.py (Alpha Vantage MCP wrapper)
│   ├── validators.py (Input validation)
│   └── formatters.py (Response formatting)
├── tests/
│   ├── test_agents.py
│   └── test_mcp_integration.py
└── main.py (Entry point)
```

---

## 🔧 Alpha Vantage MCP Integration Patterns

### ✅ DO: MCP-First Data Access

**Pattern 1: Stock Quote via MCP**

```python
from alpha_vantage_mcp import AlphaVantageMCP
from pydantic import BaseModel

class StockQuoteRequest(BaseModel):
    symbol: str
    include_extended: bool = False

async def fetch_stock_quote(request: StockQuoteRequest):
    """
    Fetch real-time stock quote through MCP.
    Returns structured market data with rate-limit awareness.
    """
    mcp = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))
    
    try:
        quote_data = await mcp.get_stock_quote(
            symbol=request.symbol.upper(),
            extended=request.include_extended
        )
        return {
            "symbol": quote_data["01. symbol"],
            "price": float(quote_data["05. price"]),
            "volume": int(quote_data["06. volume"]),
            "change_pct": float(quote_data["10. change percent"]),
            "timestamp": quote_data["07. latest trading day"]
        }
    except RateLimitError:
        # MCP handles rate limiting – return cached or retry strategy
        raise
    except ValueError as e:
        raise ValueError(f"Invalid symbol '{request.symbol}': {e}")
```

**Pattern 2: Company Information (Fundamental Data)**

```python
async def get_company_fundamental(symbol: str):
    """
    Retrieve company sector, market cap, and fundamental metrics.
    Essential for agent decision-making.
    """
    mcp = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))
    
    company = await mcp.get_company_info(symbol=symbol.upper())
    
    return {
        "name": company["Name"],
        "sector": company["Sector"],
        "industry": company["Industry"],
        "market_cap": parse_large_number(company["MarketCapitalization"]),
        "pe_ratio": float(company.get("PERatio", 0)) or None,
        "dividend_yield": float(company.get("DividendYield", 0)) or None,
        "52_week_high": float(company["52WeekHigh"]),
        "52_week_low": float(company["52WeekLow"])
    }
```

**Pattern 3: Historical Data for Analysis**

```python
async def get_historical_analysis(
    symbol: str, 
    interval: str = "daily",  # or "60min", "weekly"
    periods: int = 30
):
    """
    Fetch historical OHLCV data through MCP.
    Interval: 'daily', 'weekly', '60min', '15min'
    """
    mcp = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))
    
    timeseries = await mcp.get_historical_data(
        symbol=symbol.upper(),
        interval=interval,
        outputsize="full"  # Full 20+ years of daily data
    )
    
    # Slice to requested periods
    data_points = sorted(timeseries.items())[:periods]
    
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "data": [
            {
                "timestamp": ts,
                "open": float(ohlcv["1. open"]),
                "high": float(ohlcv["2. high"]),
                "low": float(ohlcv["3. low"]),
                "close": float(ohlcv["4. close"]),
                "volume": int(ohlcv["5. volume"])
            }
            for ts, ohlcv in data_points
        ]
    }
```

**Pattern 4: Crypto Exchange Rates (Real-Time)**

```python
async def get_crypto_rate(from_currency: str, to_currency: str = "USD"):
    """
    Real-time crypto exchange rates via MCP.
    """
    mcp = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))
    
    rate_data = await mcp.get_crypto_rates(
        from_currency=from_currency.upper(),
        to_currency=to_currency.upper()
    )
    
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "bid": float(rate_data["Realtime Currency Exchange Rate"]["1. From_Currency Code"]),
        "ask": float(rate_data["Realtime Currency Exchange Rate"]["2. To_Currency Code"]),
        "last_refreshed": rate_data["Realtime Currency Exchange Rate"]["6. Last Refreshed"]
    }
```

### ❌ DON'T: Direct HTTP API Calls

```python
# ❌ WRONG - Don't make raw HTTP requests to Alpha Vantage
import requests
response = requests.get("https://www.alphavantage.co/query", params={...})

# ✅ RIGHT - Use MCP for built-in error handling, rate limiting, caching
mcp = AlphaVantageMCP(api_key=...)
data = await mcp.get_stock_quote(symbol="AAPL")
```

---

## 🤖 Agent Architecture Patterns

### Base Agent Initialization

```python
from agents_sdk import Agent, AgentConfig
from typing import Optional

class MarketAnalysisAgent(Agent):
    def __init__(
        self,
        name: str = "market_analyst",
        model: str = "claude-opus",  # Latest reasoning model
        temperature: float = 0.7,  # Balanced creativity/consistency
        max_iterations: int = 10,
    ):
        self.mcp = AlphaVantageMCP(api_key=os.getenv("ALPHAVANTAGE_API_KEY"))
        
        self.config = AgentConfig(
            name=name,
            model=model,
            temperature=temperature,
            max_iterations=max_iterations,
            tools=self._register_mcp_tools(),
        )
        
        super().__init__(self.config)
    
    def _register_mcp_tools(self) -> list:
        """Auto-register all MCP tools as agent tools."""
        return [
            Tool(
                name="get_stock_quote",
                description="Real-time stock price, volume, and changes",
                func=self.mcp.get_stock_quote,
                required_args=["symbol"]
            ),
            Tool(
                name="get_company_info",
                description="Fundamental company data: sector, market cap, ratios",
                func=self.mcp.get_company_info,
                required_args=["symbol"]
            ),
            Tool(
                name="get_historical_data",
                description="OHLCV historical data for technical analysis",
                func=self.mcp.get_historical_data,
                required_args=["symbol"],
                optional_args=["interval", "outputsize"]
            ),
            Tool(
                name="get_crypto_rates",
                description="Real-time crypto exchange rates",
                func=self.mcp.get_crypto_rates,
                required_args=["from_currency", "to_currency"]
            ),
        ]
```

### Agent Execution Pattern (Async-First)

```python
async def analyze_stock_decision(agent: MarketAnalysisAgent, symbol: str) -> dict:
    """
    Agent orchestrates multi-step analysis:
    1. Fetch real-time quote
    2. Get company fundamentals
    3. Retrieve 90-day historical
    4. Generate investment thesis
    """
    prompt = f"""
    Analyze {symbol} for investment potential:
    1. Get real-time quote and market sentiment
    2. Retrieve company fundamentals (sector, market cap, ratios)
    3. Analyze 90-day price movement
    4. Provide 3-sentence investment thesis with risk assessment
    
    Use available market data tools. Be concise and data-driven.
    """
    
    result = await agent.run(prompt, max_iterations=5)
    
    return {
        "symbol": symbol,
        "analysis": result.output,
        "reasoning": result.reasoning_steps,
        "tool_calls": result.tool_invocations,
        "confidence": result.confidence_score
    }
```

---

## 🚀 Performance & Optimization

### Rate Limiting & Caching

```python
from functools import lru_cache
from datetime import datetime, timedelta

class ManagedAlphaVantageMCP(AlphaVantageMCP):
    """Wrapper with intelligent caching and rate-limit handling."""
    
    def __init__(self, api_key: str, cache_ttl: int = 300):
        super().__init__(api_key=api_key)
        self.cache_ttl = cache_ttl  # 5 minutes default
        self._cache = {}
        self._last_request_time = {}
    
    async def get_stock_quote(self, symbol: str, use_cache: bool = True):
        """Fetch quote with intelligent caching."""
        cache_key = f"quote:{symbol}"
        
        if use_cache and cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if datetime.now() - timestamp < timedelta(seconds=self.cache_ttl):
                return cached_data
        
        # Respect rate limits (5 calls/min free tier)
        await self._rate_limit_wait()
        
        data = await super().get_stock_quote(symbol)
        self._cache[cache_key] = (data, datetime.now())
        
        return data
    
    async def _rate_limit_wait(self):
        """Enforce rate limiting for free tier API."""
        last_time = self._last_request_time.get("last_request", 0)
        elapsed = time.time() - last_time
        
        if elapsed < 0.2:  # 5 requests per second max
            await asyncio.sleep(0.2 - elapsed)
        
        self._last_request_time["last_request"] = time.time()
```

### Error Handling & Retries

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True
)
async def fetch_with_retry(self, symbol: str):
    """Fetch with exponential backoff on rate limits."""
    try:
        return await self.mcp.get_stock_quote(symbol)
    except RateLimitError:
        logger.warning(f"Rate limit for {symbol}, retrying...")
        raise
    except ValueError as e:
        logger.error(f"Invalid request for {symbol}: {e}")
        return None
```

---

## 📊 Agent Response Patterns

### Structured Output Format

```python
from pydantic import BaseModel
from typing import List, Optional
from enum import Enum

class SentimentLevel(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"

class InvestmentThesis(BaseModel):
    symbol: str
    current_price: float
    sentiment: SentimentLevel
    key_metrics: dict
    risks: List[str]
    opportunities: List[str]
    thesis_summary: str
    confidence_score: float  # 0.0-1.0
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "AAPL",
                "current_price": 182.50,
                "sentiment": "bullish",
                "key_metrics": {
                    "pe_ratio": 28.5,
                    "market_cap": "2.8T",
                    "52_week_change": "+45%"
                },
                "risks": ["Tech sector volatility", "China exposure"],
                "opportunities": ["AI integration", "Services growth"],
                "thesis_summary": "Strong fundamentals with AI tailwinds.",
                "confidence_score": 0.78
            }
        }
```

### Agent Tool Output Handler

```python
def parse_tool_output(tool_name: str, raw_output: dict) -> dict:
    """Normalize all MCP tool outputs to consistent format."""
    
    parsers = {
        "get_stock_quote": parse_quote,
        "get_company_info": parse_company,
        "get_historical_data": parse_historical,
        "get_crypto_rates": parse_crypto,
    }
    
    if tool_name in parsers:
        return parsers[tool_name](raw_output)
    
    return raw_output
```

---

## 🎨 Code Style & Conventions

### Naming & Formatting

```python
# ✅ Agent and tool names: lowercase_with_underscores
market_analysis_agent = Agent(name="market_analyst")
get_stock_price = Tool(name="get_stock_price")

# ✅ Classes: PascalCase
class MarketAnalysisAgent(Agent):
    pass

class AlphaVantageMCPWrapper(AlphaVantageMCP):
    pass

# ✅ Constants: UPPER_SNAKE_CASE
MAX_ITERATIONS = 10
CACHE_TTL_SECONDS = 300
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

# ✅ Type hints everywhere
async def analyze_symbol(symbol: str) -> InvestmentThesis:
    """Type hints make agent reasoning clearer."""
    pass
```

### Docstring Standard (Google Style)

```python
def get_financial_metrics(symbol: str, include_technicals: bool = False) -> dict:
    """
    Fetch comprehensive financial metrics via MCP.
    
    Retrieves real-time quotes, fundamentals, and optional technical indicators.
    Respects rate limits with intelligent caching.
    
    Args:
        symbol: Stock ticker (e.g., 'AAPL')
        include_technicals: Whether to fetch 200-day moving average data
    
    Returns:
        dict: Structured financial data with keys:
            - symbol: Normalized ticker
            - price: Current price
            - fundamentals: {sector, market_cap, pe_ratio, ...}
            - technicals: (optional) 200DMA, 50DMA, RSI
    
    Raises:
        ValueError: Invalid symbol format
        RateLimitError: API rate limit exceeded (will auto-retry)
        TimeoutError: MCP connection timeout
    
    Example:
        >>> metrics = await get_financial_metrics("AAPL")
        >>> print(metrics["price"], metrics["fundamentals"]["sector"])
    """
```

---

## ⚙️ Environment & Configuration

### `.env` Template (NEVER commit secrets)

```bash
# Alpha Vantage API Configuration
ALPHAVANTAGE_API_KEY=your_free_or_premium_key_here

# Agent Configuration
AGENT_MODEL=claude-opus  # or claude-sonnet
AGENT_TEMPERATURE=0.7
AGENT_MAX_ITERATIONS=10

# Optional: Proxy or custom API endpoint
ALPHAVANTAGE_BASE_URL=https://www.alphavantage.co  # default

# Caching
CACHE_TTL_SECONDS=300
ENABLE_REQUEST_CACHING=true
```

### Loading Configuration

```python
import os
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    "alphavantage_api_key": os.getenv("ALPHAVANTAGE_API_KEY"),
    "agent_model": os.getenv("AGENT_MODEL", "claude-opus"),
    "agent_temperature": float(os.getenv("AGENT_TEMPERATURE", 0.7)),
    "agent_max_iterations": int(os.getenv("AGENT_MAX_ITERATIONS", 10)),
    "cache_ttl": int(os.getenv("CACHE_TTL_SECONDS", 300)),
}

# Validate critical config
if not CONFIG["alphavantage_api_key"]:
    raise ValueError("ALPHAVANTAGE_API_KEY not set in environment")
```

---

## 🧪 Testing Patterns

### Unit Test Template

```python
import pytest
from agents_sdk import Agent

@pytest.mark.asyncio
async def test_stock_quote_via_mcp():
    """Test MCP stock quote tool."""
    mcp = AlphaVantageMCP(api_key=TEST_API_KEY)
    quote = await mcp.get_stock_quote("AAPL")
    
    assert quote["symbol"] == "AAPL"
    assert "price" in quote
    assert float(quote["price"]) > 0

@pytest.mark.asyncio
async def test_agent_market_analysis():
    """Test agent orchestrates multiple MCP calls."""
    agent = MarketAnalysisAgent()
    result = await agent.run(
        "Analyze TSLA with recent price data and fundamentals"
    )
    
    assert "TSLA" in result.output.upper()
    assert len(result.tool_invocations) >= 2  # At least quote + company info

@pytest.mark.asyncio
async def test_rate_limit_handling():
    """Test graceful rate limit retry."""
    agent = MarketAnalysisAgent()
    
    # Should not crash on rate limit
    result = await fetch_with_retry("AAPL")
    
    assert result is not None or result is None  # Either succeeds or handles gracefully
```

---

## 🚫 Anti-Patterns (NEVER Do This)

```python
# ❌ Direct HTTP requests instead of MCP
import requests
resp = requests.get(f"https://www.alphavantage.co/query?function=QUOTE&symbol={symbol}")

# ❌ Blocking calls in async context
def fetch_data(symbol):  # Should be async
    return mcp.get_stock_quote(symbol)

# ❌ Hardcoded API keys
API_KEY = "DEMO123456789"

# ❌ No error handling on tool calls
quote = await mcp.get_stock_quote(symbol)  # What if it fails?

# ❌ Building agent prompts without type safety
prompt = f"Check stock {symbol}"  # Should validate symbol first

# ❌ Ignoring rate limits
for symbol in 100_symbols:
    await mcp.get_stock_quote(symbol)  # Will hit rate limits

# ❌ Returning raw MCP output without parsing
return await mcp.get_company_info(symbol)  # Should normalize response
```

---

## 🎯 Quick Reference: Vibe Coding Checklist

When writing agent code, hit these marks:

- [ ] **MCP-First** – Use Alpha Vantage MCP for all data (no raw HTTP)
- [ ] **Type Safe** – Every function has type hints + Pydantic models
- [ ] **Async** – All I/O operations use `async`/`await`
- [ ] **Error Handling** – Try/except with meaningful logging
- [ ] **Caching** – Smart TTL-based caching for repeated queries
- [ ] **Rate Limit Aware** – Respect free tier (5 calls/min), implement backoff
- [ ] **Structured Output** – Agent responses follow Pydantic models
- [ ] **Documented** – Google-style docstrings with examples
- [ ] **Tested** – Unit tests for agent behavior + MCP integration
- [ ] **Secrets Safe** – `.env` for API keys, never commit them
- [ ] **Readable** – Clear variable names, logical flow, comments where unclear
- [ ] **Performant** – Cache hits before API calls, batch requests where possible

---

## 📚 Resources & References

- **Alpha Vantage MCP**: https://github.com/QuantML/alpha-vantage-mcp
- **Agents SDK Docs**: Provided in your dependencies
- **Cursor Agent Guide**: https://cursor.com/docs/context/rules
- **MCP Protocol Spec**: https://modelcontextprotocol.io/
- **Financial Data Best Practices**: https://www.alphavantage.co/documentation/

---

**Last Updated:** December 2025 | **Optimized for:** Cursor Agent + Alpha Vantage MCP v1.0+
