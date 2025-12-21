"""
Synthesis agent that consolidates research outputs into a final business model report.
"""

import os
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from agents import Agent, Runner, trace, ModelSettings

load_dotenv()

# Maximum output tokens for synthesis agent (configurable via env)
# Higher limit needed since synthesis agent integrates multiple specialized research outputs
SYNTHESIS_AGENT_MAX_OUTPUT_TOKENS = int(
    os.getenv("SYNTHESIS_AGENT_MAX_OUTPUT_TOKENS", "8000")
)


class SynthesisAgent:
    """Agent that synthesizes multiple research outputs into a comprehensive report."""
    
    def __init__(self, api_key: str = None):
        """
        Initialize the synthesis agent.
        
        Args:
            api_key: OpenAI API key (optional)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")
    
    def synthesize_report(
        self,
        ticker: str,
        trade_type: str,
        research_outputs: Dict[str, Dict[str, Any]],
        context: str = ""
    ) -> str:
        """
        Synthesize all research outputs into a final business model report.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
            research_outputs: Dictionary mapping subject_id -> research results
            context: Additional context from followup questions
        
        Returns:
            Complete synthesized report text
        """
        # Build synthesis prompt
        synthesis_prompt = self._build_synthesis_prompt(
            ticker,
            trade_type,
            research_outputs,
            context
        )
        
        # Create synthesis agent
        agent = Agent(
            name="Synthesis Agent",
            instructions=self._get_synthesis_instructions(ticker, trade_type),
            model="gpt-4o",
            tools=[],  # Synthesis agent doesn't need tools, it works with provided data
            model_settings=ModelSettings(
                temperature=0.7,
                max_output_tokens=SYNTHESIS_AGENT_MAX_OUTPUT_TOKENS
            )
        )
        
        # Execute synthesis
        try:
            with trace("Report Synthesis", metadata={
                "ticker": ticker,
                "trade_type": trade_type,
                "subjects_count": str(len(research_outputs))  # Fixed: cast to string for tracing API
            }):
                # Run in a thread to avoid event loop conflicts (similar to specialized agents)
                # This ensures Runner.run_sync() works even when called from a context with an active event loop
                def _run_synthesis_in_thread():
                    return Runner.run_sync(agent, synthesis_prompt, max_turns=10)
                
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_synthesis_in_thread)
                    result = future.result()
            
            # Extract output
            if hasattr(result, 'final_output'):
                report = result.final_output
            elif hasattr(result, 'output'):
                report = result.output
            elif isinstance(result, str):
                report = result
            else:
                report = str(result)
            
            return report
            
        except Exception as e:
            error_msg = f"Error synthesizing report: {str(e)}"
            print(error_msg)
            return error_msg
    
    def _get_synthesis_instructions(self, ticker: str, trade_type: str) -> str:
        """
        Get system instructions for the synthesis agent.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
        
        Returns:
            System instructions string
        """
        from src.date_utils import get_datetime_context_string
        
        # Get current date/time context
        datetime_context = get_datetime_context_string()
        
        return f"""You are a senior equity research analyst synthesizing specialized research findings into a comprehensive business model report for {ticker}.

{datetime_context}

**Your Task:**
Integrate and structure research findings from multiple specialized research agents into a comprehensive, detailed business model report. Your role is to PRESERVE and ORGANIZE all detailed information, NOT to summarize or condense it.

**CRITICAL: Detail Preservation Requirements**
- **PRESERVE ALL SPECIFIC DATA**: Include all metrics, numbers, percentages, dollar amounts, and quantitative data points from the research outputs
- **PRESERVE ALL FACTS**: Include all specific facts, findings, and qualitative insights from specialized agents
- **PRESERVE ALL EXAMPLES**: Include specific examples, case studies, and concrete details provided by research agents
- **INTEGRATE, DON'T SUMMARIZE**: Your job is to integrate information into a structured format, not to condense or summarize away details
- **MINIMUM DETAIL REQUIREMENTS**: Each major section should include at least 3-5 specific data points, metrics, or detailed facts from the research outputs
- **INCLUDE DIRECT QUOTES**: When key metrics or critical findings are provided, include them directly with their exact values/statements
- **CROSS-REFERENCE INFORMATION**: Where information from different research subjects relates to each other, make those connections explicit

**Report Structure:**
1. **Executive Summary** - Comprehensive overview including key metrics and main findings
2. **Products and Services** - Detailed description of all products/services with specific features, capabilities, and details
3. **Revenue Breakdown** - Detailed revenue analysis with specific numbers, percentages, trends, and segment breakdowns
4. **Value Propositions and Key Clients** - Detailed value propositions with specific examples, major clients, and customer relationship details
5. **Buying Process** - Comprehensive description of the buying process with specific details about decision-makers, sales cycles, and procurement processes
6. **Seasonality** - Detailed seasonal patterns with specific quarterly data, trends, and cyclical factors
7. **Margin Structure** - Detailed margin analysis with specific percentages, trends, and segment-level profitability data
8. **Business Model Overview** - Integrated view connecting all aspects with specific details and metrics
9. **Sources and Citations** - All sources cited from the research with proper attribution

**Trade Type Context:** {trade_type}
- Adjust report depth and focus based on trade type
- For Day Trade: Emphasize immediate, actionable insights while preserving all relevant details
- For Swing Trade: Emphasize near-term factors while maintaining comprehensive detail
- For Investment: Provide comprehensive, long-term analysis with full detail preservation

**Guidelines:**
- **INTEGRATION OVER SUMMARIZATION**: Integrate all research findings seamlessly while preserving their depth and detail
- **DATA POINT PRESERVATION**: Include specific numbers, percentages, dollar amounts, dates, and quantitative metrics from research outputs
- **FACT PRESERVATION**: Include all specific facts, findings, examples, and qualitative insights
- **STRUCTURE WITHOUT REDUCTION**: Organize information into the report structure without losing detail or condensing content
- **CONNECTIONS AND CONTEXT**: Draw connections between different research subjects where relevant, using the detailed information provided
- Maintain consistency across sections while preserving all unique details
- Cite all sources from the research outputs with proper attribution
- Use clear, professional language while maintaining comprehensive detail
- Structure the report for easy reading and reference without sacrificing information density

**Important:**
- Only use information provided in the research outputs
- Do not add information not present in the research findings
- **DO NOT summarize away details** - preserve all specific metrics, numbers, and facts
- Clearly cite sources for all claims
- If information is missing for a section, note it clearly
- **Ensure the report is comprehensive, detailed, and fully utilizes all research findings**"""
    
    def _build_synthesis_prompt(
        self,
        ticker: str,
        trade_type: str,
        research_outputs: Dict[str, Dict[str, Any]],
        context: str
    ) -> str:
        """
        Build the synthesis prompt with all research outputs.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
            research_outputs: Dictionary of research results
            context: Additional context
        
        Returns:
            Formatted synthesis prompt
        """
        from src.date_utils import get_datetime_context_string
        
        # Get current date/time context
        datetime_context = get_datetime_context_string()
        
        prompt_parts = [
            f"**TASK: Synthesize specialized research findings into a comprehensive business model report for {ticker} ({trade_type})**",
            "",
            datetime_context,
            "",
            "**CRITICAL INSTRUCTIONS - READ CAREFULLY:**",
            "",
            f"The specialized research agents below have conducted detailed, in-depth research on different aspects of {ticker}'s business model. Each agent has provided comprehensive findings with specific data points, metrics, numbers, facts, and detailed analysis.",
            "",
            "**YOUR RESPONSIBILITY:**",
            "- **PRESERVE ALL DETAILS**: Include ALL specific metrics, numbers, percentages, dollar amounts, dates, and quantitative data from each research output",
            "- **PRESERVE ALL FACTS**: Include ALL specific facts, findings, examples, and qualitative insights from each research output",
            "- **INTEGRATE, DON'T SUMMARIZE**: Your job is to integrate this detailed information into a well-structured report, NOT to condense or summarize away the details",
            "- **USE ALL INFORMATION**: Fully utilize all the detailed research findings - specialized agents have done comprehensive work that should be preserved in the final report",
            "- **MAINTAIN DEPTH**: Maintain the depth and specificity of analysis provided by the specialized research agents",
            "- **INCLUDE SPECIFIC DATA**: Each section of your report should include specific data points, metrics, and detailed information - avoid high-level summaries",
            "- **CROSS-REFERENCE**: Where information from different research subjects connects, make those relationships explicit using the detailed data provided",
            "",
            "The specialized agents have invested significant effort in gathering detailed information. Your synthesis should reflect and preserve this comprehensive research, not reduce it to bullet points or high-level summaries.",
            "",
            "**Research Findings from Specialized Agents:**",
            ""
        ]
        
        # Add each research output
        for subject_id, result in research_outputs.items():
            subject_name = result.get("subject_name", subject_id)
            research_output = result.get("research_output", "No research output available")
            sources = result.get("sources", [])
            
            prompt_parts.append(f"### {subject_name} - Detailed Research Output")
            prompt_parts.append("")
            prompt_parts.append("**Comprehensive Research Findings (preserve all details from this output):**")
            prompt_parts.append(research_output)
            
            if sources:
                prompt_parts.append("")
                prompt_parts.append("**Sources Used by This Research Agent:**")
                for i, source in enumerate(sources, 1):
                    prompt_parts.append(f"{i}. {source}")
            
            prompt_parts.append("")
            prompt_parts.append("---")
            prompt_parts.append("")
        
        if context:
            prompt_parts.append("")
            prompt_parts.append("**Additional Context from User:**")
            prompt_parts.append(context)
            prompt_parts.append("")
        
        prompt_parts.append("")
        prompt_parts.append("**FINAL INSTRUCTIONS:**")
        prompt_parts.append("")
        prompt_parts.append("Now create a comprehensive, detailed business model report that:")
        prompt_parts.append("1. Integrates all the detailed research findings above into a well-structured format")
        prompt_parts.append("2. Preserves ALL specific metrics, numbers, facts, and detailed information from each research output")
        prompt_parts.append("3. Includes specific data points (percentages, dollar amounts, dates, quantities) throughout the report")
        prompt_parts.append("4. Maintains the depth and comprehensiveness of the specialized research")
        prompt_parts.append("5. Clearly cites all sources from the research outputs")
        prompt_parts.append("6. Draws connections between different research subjects where relevant")
        prompt_parts.append("")
        prompt_parts.append("Remember: The goal is comprehensive integration of detailed information, NOT summarization or condensation. Use all the detailed findings provided by the specialized research agents.")
        
        return "\n".join(prompt_parts)

