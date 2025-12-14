"""
Synthesis agent that consolidates research outputs into a final business model report.
"""

import os
from typing import Dict, Any, List
from dotenv import load_dotenv

from agents import Agent, Runner, trace, ModelSettings

load_dotenv()


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
            model_settings=ModelSettings(temperature=0.7)
        )
        
        # Execute synthesis
        try:
            with trace("Report Synthesis", metadata={
                "ticker": ticker,
                "trade_type": trade_type,
                "subjects_count": len(research_outputs)
            }):
                result = Runner.run_sync(
                    agent,
                    synthesis_prompt,
                    max_turns=10
                )
            
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
        return f"""You are a senior equity research analyst synthesizing specialized research findings into a comprehensive business model report for {ticker}.

**Your Task:**
Consolidate research findings from multiple specialized research agents into a cohesive, well-structured business model report.

**Report Structure:**
1. **Executive Summary** - Brief overview of the business model
2. **Products and Services** - What the business offers
3. **Revenue Breakdown** - Revenue by product, geography, and channel
4. **Value Propositions and Key Clients** - Value propositions and customer relationships
5. **Buying Process** - Who buys and how the buying process works
6. **Seasonality** - Seasonal patterns and cyclicality
7. **Margin Structure** - Margin structure by segment
8. **Business Model Overview** - Integrated view of how the business operates
9. **Sources and Citations** - All sources cited from the research

**Trade Type Context:** {trade_type}
- Adjust report depth and focus based on trade type
- For Day Trade: Emphasize immediate, actionable insights
- For Swing Trade: Emphasize near-term factors
- For Investment: Provide comprehensive, long-term analysis

**Guidelines:**
- Integrate all research findings seamlessly
- Maintain consistency across sections
- Cite all sources from the research outputs
- Highlight key insights and data points
- Ensure the report maps the entire business model clearly
- Use clear, professional language
- Structure the report for easy reading and reference

**Important:**
- Only use information provided in the research outputs
- Do not add information not present in the research findings
- Clearly cite sources for all claims
- If information is missing for a section, note it clearly
- Ensure the report is comprehensive and actionable"""
    
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
        prompt_parts = [
            f"Synthesize the following specialized research findings into a comprehensive business model report for {ticker} ({trade_type}).",
            "",
            "**Research Findings:**",
            ""
        ]
        
        # Add each research output
        for subject_id, result in research_outputs.items():
            subject_name = result.get("subject_name", subject_id)
            research_output = result.get("research_output", "No research output available")
            sources = result.get("sources", [])
            
            prompt_parts.append(f"### {subject_name}")
            prompt_parts.append("")
            prompt_parts.append("Research Output:")
            prompt_parts.append(research_output)
            
            if sources:
                prompt_parts.append("")
                prompt_parts.append("Sources:")
                for i, source in enumerate(sources, 1):
                    prompt_parts.append(f"{i}. {source}")
            
            prompt_parts.append("")
            prompt_parts.append("---")
            prompt_parts.append("")
        
        if context:
            prompt_parts.append("")
            prompt_parts.append("**Additional Context:**")
            prompt_parts.append(context)
            prompt_parts.append("")
        
        prompt_parts.append("")
        prompt_parts.append("Now synthesize all these findings into a comprehensive, well-structured business model report with all sources cited.")
        
        return "\n".join(prompt_parts)

