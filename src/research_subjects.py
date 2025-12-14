"""
Research subject definitions for specialized agent research.
"""

from typing import Dict, List
from dataclasses import dataclass


@dataclass
class ResearchSubject:
    """Represents a research subject for specialized agent research."""
    id: str
    name: str
    description: str
    prompt_template: str


# Research subject definitions
PRODUCTS_SERVICES = ResearchSubject(
    id="products_services",
    name="Products and Services",
    description="What products/services does the business offer?",
    prompt_template="""Research and provide comprehensive information about {ticker}'s products and services.

Focus on:
- Complete product/service portfolio
- Product categories and lines
- Service offerings
- Key features and capabilities
- Product lifecycle stages
- Innovation and R&D focus areas

Use Alpha Vantage MCP tools for company overview and financial data, and Perplexity research for detailed product information, market positioning, and recent product launches or updates."""
)

REVENUE_BREAKDOWN = ResearchSubject(
    id="revenue_breakdown",
    name="Revenue Breakdown",
    description="Revenue breakdown by product, geography, and channel",
    prompt_template="""Research and provide detailed revenue breakdown for {ticker}.

Focus on:
- Revenue by product/service line
- Revenue by geographic region
- Revenue by sales channel (direct, indirect, online, retail, etc.)
- Revenue trends and growth rates by segment
- Percentage contribution of each segment
- Historical revenue mix changes

Use Alpha Vantage MCP tools for financial statements and income statements. Use Perplexity research for detailed segment reporting, geographic revenue data, and channel-specific information from company reports and filings."""
)

VALUE_PROPOSITIONS = ResearchSubject(
    id="value_propositions",
    name="Value Propositions and Key Clients",
    description="Value propositions and key clients",
    prompt_template="""Research {ticker}'s value propositions and key clients.

Focus on:
- Core value propositions for each product/service
- Unique selling points and competitive advantages
- Key customer segments
- Major clients and customer relationships
- Customer retention and loyalty metrics
- Market positioning and brand value

Use Alpha Vantage MCP tools for company overview. Use Perplexity research for customer case studies, client lists, value proposition details, and market positioning information."""
)

BUYING_PROCESS = ResearchSubject(
    id="buying_process",
    name="Buying Process",
    description="Who buys and how does the buying process work?",
    prompt_template="""Research {ticker}'s customer buying process and decision-makers.

Focus on:
- Primary customer types and personas
- Decision-making process and stakeholders
- Sales cycle length and stages
- Buying criteria and evaluation factors
- Procurement processes
- Customer acquisition channels
- Relationship management approach

Use Perplexity research for detailed information about customer buying behavior, sales processes, and industry-specific procurement patterns."""
)

SEASONALITY = ResearchSubject(
    id="seasonality",
    name="Seasonality",
    description="Seasonality patterns",
    prompt_template="""Research seasonal patterns and cyclicality for {ticker}.

Focus on:
- Quarterly revenue patterns
- Seasonal demand fluctuations
- Industry-specific seasonality
- Historical seasonal trends
- Peak and off-peak periods
- Factors driving seasonality
- Impact of seasonality on operations and cash flow

Use Alpha Vantage MCP tools for quarterly earnings and financial data. Use Perplexity research for industry-specific seasonal patterns and analysis."""
)

MARGIN_STRUCTURE = ResearchSubject(
    id="margin_structure",
    name="Margin Structure",
    description="Margin structure by segment",
    prompt_template="""Research {ticker}'s margin structure by business segment.

Focus on:
- Gross margins by product/service line
- Operating margins by segment
- Profitability by geography
- Margin trends over time
- Factors affecting margins
- Cost structure by segment
- Margin improvement initiatives

Use Alpha Vantage MCP tools for income statements and financial data. Use Perplexity research for detailed segment margin analysis and cost structure information."""
)


def get_research_subjects() -> List[ResearchSubject]:
    """
    Get all research subjects.
    
    Returns:
        List of ResearchSubject objects
    """
    return [
        PRODUCTS_SERVICES,
        REVENUE_BREAKDOWN,
        VALUE_PROPOSITIONS,
        BUYING_PROCESS,
        SEASONALITY,
        MARGIN_STRUCTURE
    ]


def get_research_subject_by_id(subject_id: str) -> ResearchSubject:
    """
    Get a research subject by ID.
    
    Args:
        subject_id: Subject ID
    
    Returns:
        ResearchSubject object
    
    Raises:
        ValueError: If subject ID not found
    """
    for subject in get_research_subjects():
        if subject.id == subject_id:
            return subject
    raise ValueError(f"Research subject not found: {subject_id}")


def format_subject_prompt(subject: ResearchSubject, ticker: str, trade_type: str, context: str = "") -> str:
    """
    Format a research subject prompt with ticker and context.
    
    Args:
        subject: ResearchSubject object
        ticker: Stock ticker symbol
        trade_type: Type of trade
        context: Additional context from followup questions
    
    Returns:
        Formatted prompt string
    """
    base_prompt = subject.prompt_template.format(ticker=ticker)
    
    if context:
        base_prompt += f"\n\nAdditional context from user: {context}"
    
    return base_prompt

