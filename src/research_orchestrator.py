"""
Research orchestrator for coordinating parallel specialized research agents.
"""

from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from research_subjects import get_research_subjects, format_subject_prompt
from specialized_agent import SpecializedResearchAgent


class ResearchOrchestrator:
    """Orchestrates parallel research across multiple specialized agents."""
    
    def __init__(self, api_key: str = None):
        """
        Initialize the research orchestrator.
        
        Args:
            api_key: OpenAI API key (optional)
        """
        self.api_key = api_key
    
    def craft_research_prompts(
        self,
        ticker: str,
        trade_type: str,
        context: str = ""
    ) -> Dict[str, str]:
        """
        Craft research prompts for all research subjects.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
            context: Additional context from followup questions
        
        Returns:
            Dictionary mapping subject_id -> formatted prompt
        """
        subjects = get_research_subjects()
        prompts = {}
        
        for subject in subjects:
            prompt = format_subject_prompt(subject, ticker, trade_type, context)
            prompts[subject.id] = prompt
        
        return prompts
    
    def run_parallel_research(
        self,
        ticker: str,
        trade_type: str,
        context: str = "",
        max_workers: int = 6
    ) -> Dict[str, Dict[str, Any]]:
        """
        Execute parallel research using specialized agents.
        
        Args:
            ticker: Stock ticker symbol
            trade_type: Type of trade
            context: Additional context from followup questions
            max_workers: Maximum number of parallel workers (default: 6)
        
        Returns:
            Dictionary mapping subject_id -> research results
        """
        subjects = get_research_subjects()
        results = {}
        
        print(f"Starting parallel research for {ticker} ({trade_type})...")
        print(f"Researching {len(subjects)} subjects in parallel...")
        
        start_time = time.time()
        
        # Use ThreadPoolExecutor for parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all research tasks
            future_to_subject = {}
            
            for subject in subjects:
                agent = SpecializedResearchAgent(api_key=self.api_key)
                future = executor.submit(
                    agent.research_subject,
                    ticker,
                    subject,
                    trade_type,
                    context
                )
                future_to_subject[future] = subject
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_subject):
                subject = future_to_subject[future]
                try:
                    result = future.result()
                    results[subject.id] = result
                    completed += 1
                    print(f"✓ Completed research for: {subject.name} ({completed}/{len(subjects)})")
                except Exception as e:
                    print(f"✗ Error researching {subject.name}: {e}")
                    results[subject.id] = {
                        "subject_id": subject.id,
                        "subject_name": subject.name,
                        "research_output": f"Error: {str(e)}",
                        "sources": [],
                        "ticker": ticker,
                        "trade_type": trade_type,
                        "error": str(e)
                    }
                    completed += 1
        
        elapsed_time = time.time() - start_time
        print(f"✓ Parallel research completed in {elapsed_time:.2f} seconds")
        
        return results
    
    def get_research_summary(self, results: Dict[str, Dict[str, Any]]) -> str:
        """
        Generate a summary of research results.
        
        Args:
            results: Dictionary of research results from parallel execution
        
        Returns:
            Summary string
        """
        summary_lines = ["Research Summary:"]
        summary_lines.append(f"Total subjects researched: {len(results)}")
        
        successful = sum(1 for r in results.values() if "error" not in r)
        summary_lines.append(f"Successful: {successful}/{len(results)}")
        
        if successful < len(results):
            failed = [r["subject_name"] for r in results.values() if "error" in r]
            summary_lines.append(f"Failed subjects: {', '.join(failed)}")
        
        return "\n".join(summary_lines)

