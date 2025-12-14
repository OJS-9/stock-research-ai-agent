# Perplexity Sonar Integration Upgrade Plan

## 📋 Overview

This document outlines the plan to upgrade the Stock Portfolio Agent to integrate Perplexity's Sonar API (`sonar::M` model) and research functions alongside the existing Alpha Vantage MCP integration. This will enable the agent to perform real-time web research and combine it with structured financial data.

---

## 🎯 Goals

1. **Integrate Perplexity Sonar API** - Add real-time web search and research capabilities
2. **Maintain Alpha Vantage Integration** - Keep existing financial data tools working
3. **Hybrid Research Approach** - Combine structured financial data (Alpha Vantage) with real-time web research (Perplexity)
4. **Seamless Agent Experience** - Agent automatically chooses the right tool for each research task

---

## 🏗️ Architecture Changes

### Current Architecture
```
User Request
    ↓
StockResearchAgent (OpenAI GPT-4o)
    ↓
Alpha Vantage MCP Tools (Financial Data)
    ↓
Response
```

### Upgraded Architecture
```
User Request
    ↓
StockResearchAgent (OpenAI GPT-4o)
    ↓
    ├─→ Alpha Vantage MCP Tools (Financial Data)
    └─→ Perplexity Sonar Research Function (Web Research)
    ↓
Combined Response
```

---

## 📦 Implementation Plan

### Phase 1: Perplexity Client Setup

#### 1.1 Create Perplexity Client Module
**File:** `src/perplexity_client.py`

**Purpose:** Create an AsyncOpenAI client configured for Perplexity's Sonar API endpoint.

**Key Components:**
- AsyncOpenAI client with custom base URL (`https://api.perplexity.ai`)
- API key management from environment variables
- Support for different Sonar models (`sonar::M`, `sonar::L`, etc.)
- Error handling and rate limiting

**Implementation Pattern:**
```python
from openai import AsyncOpenAI
import os

class PerplexityClient:
    def __init__(self, api_key: Optional[str] = None, model: str = "sonar"):
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        self.model = model
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://api.perplexity.ai"
        )
```

#### 1.2 Environment Configuration
**File:** `.env` (update)

**Add:**
```bash
PERPLEXITY_API_KEY=your-perplexity-api-key-here
PERPLEXITY_MODEL=sonar  # or sonar::M, sonar::L, etc.
```

---

### Phase 2: Research Function Tool

#### 2.1 Create Research Function
**File:** `src/perplexity_tools.py`

**Purpose:** Create OpenAI function definition and execution handler for Perplexity research.

**Key Components:**
- Function definition for OpenAI function calling
- Async execution function that calls Perplexity API
- Response formatting and error handling

**Function Definition:**
```python
def get_perplexity_research_function() -> Dict[str, Any]:
    """Get OpenAI function definition for Perplexity research."""
    return {
        "type": "function",
        "function": {
            "name": "perplexity_research",
            "description": (
                "Perform real-time web research on a topic using Perplexity's Sonar API. "
                "Use this for finding recent news, market analysis, company developments, "
                "industry trends, and other information not available in structured financial data. "
                "Returns comprehensive, cited research results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Research query or question to investigate"
                    },
                    "focus": {
                        "type": "string",
                        "enum": ["news", "analysis", "general", "financial"],
                        "description": "Focus area for the research",
                        "default": "general"
                    }
                },
                "required": ["query"]
            }
        }
    }
```

**Execution Function:**
```python
async def execute_perplexity_research(
    perplexity_client: PerplexityClient,
    query: str,
    focus: str = "general"
) -> Dict[str, Any]:
    """Execute Perplexity research query."""
    # Format query based on focus
    formatted_query = _format_query(query, focus)
    
    # Call Perplexity API
    response = await perplexity_client.client.chat.completions.create(
        model=perplexity_client.model,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful research assistant that provides accurate, cited information."
            },
            {
                "role": "user",
                "content": formatted_query
            }
        ],
        temperature=0.2,  # Lower temperature for factual research
        max_tokens=2000
    )
    
    return {
        "query": query,
        "research": response.choices[0].message.content,
        "citations": response.citations if hasattr(response, 'citations') else []
    }
```

---

### Phase 3: Agent Integration

#### 3.1 Update StockResearchAgent
**File:** `src/agent.py`

**Changes:**
1. Add Perplexity client initialization
2. Include Perplexity research function in tool list
3. Update tool execution to handle both MCP and Perplexity tools
4. Make agent methods async where needed

**Key Updates:**

```python
class StockResearchAgent:
    def __init__(self, api_key: Optional[str] = None):
        # ... existing initialization ...
        
        # Initialize Perplexity client
        from src.perplexity_client import PerplexityClient
        self.perplexity_client = PerplexityClient()
        
        # Add Perplexity research tool
        from src.perplexity_tools import get_perplexity_research_function
        self.perplexity_tool = get_perplexity_research_function()
        
        # Combine tools
        self.all_tools = self.mcp_tools + [self.perplexity_tool]
```

#### 3.2 Update Tool Execution Logic
**File:** `src/agent.py` - `_get_response_with_tools` method

**Changes:**
- Handle both MCP tools and Perplexity research tool
- Route tool calls to appropriate handler
- Support async execution for Perplexity calls

**Pattern:**
```python
async def _execute_tool_call(self, tool_call):
    """Execute a tool call, routing to appropriate handler."""
    function_name = tool_call.function.name
    
    if function_name == "perplexity_research":
        # Execute Perplexity research
        from src.perplexity_tools import execute_perplexity_research
        args = json.loads(tool_call.function.arguments)
        result = await execute_perplexity_research(
            self.perplexity_client,
            query=args["query"],
            focus=args.get("focus", "general")
        )
        return json.dumps(result, indent=2)
    else:
        # Execute MCP tool (existing logic)
        return execute_tool_by_name(...)
```

---

### Phase 4: System Instructions Update

#### 4.1 Update Research Prompts
**File:** `src/research_prompt.py`

**Changes:**
- Add guidance on when to use Perplexity research vs. Alpha Vantage tools
- Update system instructions to mention research capabilities

**New Section:**
```python
## Research Tools Available

**Alpha Vantage MCP Tools** (Structured Financial Data):
- Use for: Company fundamentals, financial statements, earnings data, balance sheets
- Best for: Quantitative analysis, historical data, financial metrics

**Perplexity Research** (Real-Time Web Research):
- Use for: Recent news, market analysis, company developments, industry trends
- Best for: Qualitative analysis, current events, competitive intelligence

**Tool Selection Strategy:**
1. Start with Alpha Vantage tools for financial fundamentals
2. Use Perplexity research for recent developments and market context
3. Combine both for comprehensive analysis
```

---

### Phase 5: Dependencies & Configuration

#### 5.1 Update Requirements
**File:** `requirements.txt`

**Add:**
```txt
# Existing dependencies...
openai>=1.0.0  # Already present, ensure async support

# No additional dependencies needed - OpenAI SDK supports Perplexity API
```

#### 5.2 Update Environment Template
**File:** `.env.example` (create if doesn't exist)

**Add:**
```bash
# Perplexity API Configuration
PERPLEXITY_API_KEY=your-perplexity-api-key-here
PERPLEXITY_MODEL=sonar  # Options: sonar, sonar::M, sonar::L
```

---

## 🔄 Migration Strategy

### Step 1: Add Perplexity Client (Non-Breaking)
- Create `src/perplexity_client.py`
- Create `src/perplexity_tools.py`
- Add environment variable (optional, graceful fallback if missing)

### Step 2: Integrate into Agent (Backward Compatible)
- Update `StockResearchAgent` to include Perplexity tool
- Keep all existing MCP functionality intact
- Add async support where needed

### Step 3: Update System Instructions
- Enhance prompts to guide tool selection
- Test with existing workflows

### Step 4: Testing & Validation
- Test Perplexity research function in isolation
- Test agent with both tools
- Verify backward compatibility

---

## 📊 Tool Selection Logic

### When to Use Alpha Vantage MCP:
- ✅ Company fundamentals (P/E ratio, market cap, revenue)
- ✅ Financial statements (income, balance sheet, cash flow)
- ✅ Earnings data and historical metrics
- ✅ Structured financial data queries

### When to Use Perplexity Research:
- ✅ Recent news and developments
- ✅ Market analysis and expert opinions
- ✅ Industry trends and competitive landscape
- ✅ Company-specific events (earnings calls, product launches)
- ✅ Macroeconomic factors affecting the stock
- ✅ Analyst reports and recommendations

### Combined Usage Example:
```
User: "Research AAPL for a swing trade"

Agent Flow:
1. Call Alpha Vantage OVERVIEW tool → Get fundamentals
2. Call Perplexity research: "Recent Apple news and market sentiment for swing trading"
3. Call Alpha Vantage NEWS_SENTIMENT → Get structured news data
4. Synthesize all information into comprehensive report
```

---

## 🚀 Advanced Features (Future Enhancements)

### 1. Streaming Responses
- Support streaming for Perplexity research (real-time updates)

### 2. Context Management
- Maintain research context across multiple queries
- Cache research results to avoid duplicate API calls

### 3. Citation Tracking
- Extract and display citations from Perplexity responses
- Link citations to research sources

### 4. Research Summarization
- Automatically summarize long research results
- Extract key insights and actionable information

### 5. Multi-Model Support
- Allow configuration of different Sonar models
- `sonar::M` for general research
- `sonar::L` for longer, more detailed research

---

## ⚠️ Important Considerations

### API Costs
- **Monitor Usage**: Both Perplexity and OpenAI Agents incur costs
- **Rate Limiting**: Implement appropriate backoff strategies
- **Caching**: Cache research results to reduce API calls

### Error Handling
- Graceful fallback if Perplexity API is unavailable
- Continue with Alpha Vantage tools only
- Log errors for monitoring

### Security
- Never commit API keys to version control
- Use environment variables for all secrets
- Validate API keys on initialization

### Rate Limits
- Perplexity API has rate limits (check current limits)
- Implement exponential backoff
- Consider request queuing for high-volume usage

---

## 📝 Implementation Checklist

### Phase 1: Foundation
- [ ] Create `src/perplexity_client.py`
- [ ] Create `src/perplexity_tools.py`
- [ ] Add `PERPLEXITY_API_KEY` to environment
- [ ] Test Perplexity client connection

### Phase 2: Integration
- [ ] Update `StockResearchAgent.__init__()` to include Perplexity client
- [ ] Add Perplexity research function to tool list
- [ ] Update `_get_response_with_tools()` to handle Perplexity calls
- [ ] Make necessary methods async

### Phase 3: Prompts & Instructions
- [ ] Update `src/research_prompt.py` with tool selection guidance
- [ ] Test agent with new instructions

### Phase 4: Testing
- [ ] Unit tests for Perplexity client
- [ ] Integration tests for agent with both tools
- [ ] End-to-end test with real research query
- [ ] Verify backward compatibility

### Phase 5: Documentation
- [ ] Update README with Perplexity setup instructions
- [ ] Document new environment variables
- [ ] Add examples of combined tool usage

---

## 🔗 Reference Links

- **Perplexity API Documentation**: https://docs.perplexity.ai/
- **Sonar Models**: https://docs.perplexity.ai/docs/models
- **OpenAI Agents SDK**: https://platform.openai.com/docs/guides/agents
- **AsyncOpenAI Client**: https://github.com/openai/openai-python

---

## 📅 Timeline Estimate

- **Phase 1**: 2-3 hours (Client setup)
- **Phase 2**: 3-4 hours (Agent integration)
- **Phase 3**: 1-2 hours (Prompt updates)
- **Phase 4**: 2-3 hours (Testing)
- **Phase 5**: 1 hour (Documentation)

**Total**: ~10-13 hours of development time

---

## 🎯 Success Criteria

1. ✅ Agent can use Perplexity research function alongside Alpha Vantage tools
2. ✅ Agent intelligently selects appropriate tool for each task
3. ✅ Research results are properly formatted and integrated into responses
4. ✅ All existing functionality remains intact
5. ✅ Error handling gracefully manages API failures
6. ✅ Documentation is updated and clear

---

**Last Updated**: December 2025  
**Status**: Planning Phase  
**Next Steps**: Begin Phase 1 implementation

