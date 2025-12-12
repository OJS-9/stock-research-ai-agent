# Stock Research AI Agent

An AI agent that performs fundamental stock research based on ticker symbol and trade type (Day Trade, Swing Trade, or Investment). The agent uses OpenAI Agents SDK with Alpha Vantage MCP server to fetch financial data and generate comprehensive research reports.

## Features

- **Interactive Research**: Agent asks follow-up questions to refine research queries
- **Trade Type Specific**: Tailored analysis for Day Trade, Swing Trade, or Investment strategies
- **MCP Integration**: Uses Alpha Vantage MCP server for real-time financial data
- **Gradio Interface**: User-friendly web UI for interaction

## Prerequisites

- Python 3.10 or higher
- OpenAI API key ([Get one here](https://platform.openai.com/api-keys))
- Alpha Vantage API key ([Get free key here](https://www.alphavantage.co/support/#api-key))

## Setup

1. **Clone or navigate to the project directory**

2. **Create a virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env and add your OPENAI_API_KEY and ALPHA_VANTAGE_API_KEY
   ```

5. **Configure MCP server**:
   ```bash
   cp mcp.json.example mcp.json
   # Edit mcp.json and replace YOUR_API_KEY with your Alpha Vantage API key
   ```

## Usage

Run the Gradio interface:
```bash
python src/gradio_app.py
```

Then:
1. Enter a stock ticker (e.g., AAPL, TSLA)
2. Select a trade type (Day Trade, Swing Trade, or Investment)
3. Answer any follow-up questions the agent asks
4. Review the generated research report

## Project Structure

```
Stock Portfolio Agent/
├── requirements.txt          # Python dependencies
├── .env                      # API keys (not committed)
├── .env.example              # Template for environment variables
├── .gitignore               # Git ignore rules
├── mcp.json                 # MCP server configuration
├── mcp.json.example         # Template for MCP config
├── README.md                # This file
└── src/
    ├── __init__.py
    ├── mcp_manager.py       # MCP server connection and management
    ├── agent.py             # OpenAI Agents SDK agent logic
    ├── research_prompt.py   # Prompt templates and system instructions
    └── gradio_app.py        # Gradio interface and main app
```

## Trade Types

### Day Trade
Focuses on intraday fundamentals: news catalysts, earnings releases, macro events, pre-market sentiment, and liquidity.

### Swing Trade (1-14 days)
Evaluates near-term fundamental factors: earnings trends, revenue revisions, analyst upgrades/downgrades, and sector momentum.

### Investment (3+ months)
Performs deep fundamental research: business model, financial statements, valuation, competitive moat, and management quality.

## License

This project is for educational and research purposes.

