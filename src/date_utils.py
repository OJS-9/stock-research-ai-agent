"""
Date and time utilities for agent prompts.
Ensures agents have current date/time context for data freshness.
"""

from datetime import datetime
from typing import Dict


def get_current_datetime_info() -> Dict[str, str]:
    """
    Get current date and time information in multiple formats for agent prompts.
    
    Returns:
        Dictionary with formatted date/time strings:
        - current_date: Full date (e.g., "December 15, 2024")
        - current_datetime: Full date and time (e.g., "December 15, 2024 at 3:45 PM EST")
        - iso_date: ISO format date (e.g., "2024-12-15")
        - day_of_week: Day name (e.g., "Sunday")
        - timezone: Timezone abbreviation (e.g., "EST")
    """
    now = datetime.now()
    
    # Format full date
    current_date = now.strftime("%B %d, %Y")
    
    # Format full date and time
    current_datetime = now.strftime("%B %d, %Y at %I:%M %p")
    
    # ISO date
    iso_date = now.strftime("%Y-%m-%d")
    
    # Day of week
    day_of_week = now.strftime("%A")
    
    # Timezone (using local timezone)
    try:
        timezone = now.strftime("%Z") or now.astimezone().tzname() or "Local"
    except:
        timezone = "Local"
    
    return {
        "current_date": current_date,
        "current_datetime": f"{current_datetime} {timezone}",
        "iso_date": iso_date,
        "day_of_week": day_of_week,
        "timezone": timezone
    }


def get_datetime_context_string() -> str:
    """
    Get a formatted string for inclusion in agent prompts.
    
    Returns:
        Formatted string with current date/time information
    """
    dt_info = get_current_datetime_info()
    
    return f"""**CURRENT DATE AND TIME:**
- Today's Date: {dt_info['current_date']} ({dt_info['day_of_week']})
- Current Date/Time: {dt_info['current_datetime']}
- ISO Date: {dt_info['iso_date']}

**IMPORTANT FOR DATA FRESHNESS:**
- Ensure all data queries and searches use the most recent information available
- When referencing dates, use {dt_info['current_date']} as the reference point
- For time-sensitive queries (earnings, news, market data), prioritize data from {dt_info['current_date']} or the most recent trading day
- When using Alpha Vantage MCP tools, request the latest available data
- When using Perplexity Research, ensure searches include recent dates and current context
- All financial data, news, and market information should be current as of {dt_info['current_date']}"""

