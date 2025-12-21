# Tool Selection Guide

## Essential Tools (Fixed List)

The agent uses only the 6 tools explicitly documented in `src/research_prompt.py` (lines 98-120):

1. **OVERVIEW** - Company overview and fundamental data (line 98)
   - Parameters: `symbol` (string, required) - Stock ticker symbol
   - Returns: Company name, description, sector, P/E ratio, revenue, EBITDA, and other fundamentals

2. **INCOME_STATEMENT** - Company income statement data (line 102)
   - Parameters: `symbol` (string, required) - Stock ticker symbol
   - Returns: Revenue, expenses, net income, and other income statement metrics

3. **BALANCE_SHEET** - Company balance sheet data (line 106)
   - Parameters: `symbol` (string, required) - Stock ticker symbol
   - Returns: Assets, liabilities, equity, and other balance sheet items

4. **CASH_FLOW** - Company cash flow statement data (line 110)
   - Parameters: `symbol` (string, required) - Stock ticker symbol
   - Returns: Operating, investing, and financing cash flows

5. **EARNINGS** - Company earnings data (line 114)
   - Parameters: `symbol` (string, required) - Stock ticker symbol
   - Returns: Quarterly and annual earnings data

6. **NEWS_SENTIMENT** - News articles and sentiment analysis (line 118)
   - Parameters: `ticker` (string, required) - Stock ticker symbol, `limit` (integer, optional) - Number of articles (default: 50)
   - Returns: Recent news articles and sentiment scores

**Total: 6 tools** - This keeps token usage well below limits while providing all essential stock research capabilities.

## Rationale

These 6 tools are the only ones explicitly documented in the agent's system instructions (`research_prompt.py`). They provide:

- **Company Fundamentals**: OVERVIEW
- **Financial Statements**: INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW
- **Performance Metrics**: EARNINGS
- **Market Sentiment**: NEWS_SENTIMENT

This focused set ensures the agent has access to all necessary data for comprehensive stock research while maintaining efficient token usage.

## Modifying Tool List

To change the tool list, update the `essential_tool_names` set in `src/agent_tools.py` in the `create_mcp_tools()` function.

## Available MCP Tools

See the full list at: https://mcp.alphavantage.co/

Common tool categories:
- **Time Series**: TIME_SERIES_INTRADAY, TIME_SERIES_DAILY, TIME_SERIES_WEEKLY, etc.
- **Fundamentals**: COMPANY_OVERVIEW, INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS
- **News**: NEWS_SENTIMENT
- **Technical Indicators**: SMA, EMA, RSI, MACD, etc.
- **Commodities**: WTI, BRENT, COPPER, etc.
- **Forex**: FX_INTRADAY, FX_DAILY, etc.
- **Crypto**: CRYPTO_INTRADAY, DIGITAL_CURRENCY_DAILY, etc.
