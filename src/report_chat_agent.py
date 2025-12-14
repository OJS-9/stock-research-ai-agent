"""
RAG-lite chat agent for answering questions about stored reports.
"""

import os
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from agents import Agent, Runner, trace, ModelSettings

from embedding_service import EmbeddingService
from vector_search import VectorSearch

load_dotenv()


class ReportChatAgent:
    """Agent for chatting with reports using RAG-lite retrieval."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the report chat agent.
        
        Args:
            api_key: OpenAI API key (optional)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")
        
        self.embedding_service = EmbeddingService(api_key=self.api_key)
        self.vector_search = VectorSearch()
        self.conversation_history: List[Dict[str, str]] = []
    
    def answer_question(
        self,
        report_id: str,
        user_question: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        top_k: int = 5
    ) -> str:
        """
        Answer a question about a report using RAG-lite retrieval.
        
        Args:
            report_id: Report ID
            user_question: User's question
            conversation_history: Previous conversation turns (optional)
            top_k: Number of chunks to retrieve
        
        Returns:
            Agent's answer based on report excerpts
        """
        # Embed the user question
        query_embedding = self.embedding_service.create_embedding(user_question)
        
        # Search for relevant chunks
        relevant_chunks = self.vector_search.search_chunks(
            report_id=report_id,
            query_embedding=query_embedding,
            top_k=top_k
        )
        
        if not relevant_chunks:
            return "I couldn't find relevant information in the report to answer your question. The report may not contain information about this topic."
        
        # Build prompt with relevant chunks
        prompt = self._build_rag_prompt(user_question, relevant_chunks, conversation_history)
        
        # Get system instructions
        system_instructions = self._get_system_instructions()
        
        # Create agent
        agent = Agent(
            name="Report Chat Agent",
            instructions=system_instructions,
            model="gpt-4o",
            tools=[],  # Chat agent doesn't need tools, works with provided context
            model_settings=ModelSettings(temperature=0.7)
        )
        
        # Execute
        try:
            with trace("Report Chat", metadata={
                "report_id": report_id,
                "chunks_retrieved": len(relevant_chunks)
            }):
                result = Runner.run_sync(
                    agent,
                    prompt,
                    max_turns=5
                )
            
            # Extract output
            if hasattr(result, 'final_output'):
                answer = result.final_output
            elif hasattr(result, 'output'):
                answer = result.output
            elif isinstance(result, str):
                answer = result
            else:
                answer = str(result)
            
            return answer
            
        except Exception as e:
            error_msg = f"Error generating answer: {str(e)}"
            print(error_msg)
            return error_msg
    
    def _get_system_instructions(self) -> str:
        """Get system instructions for the chat agent."""
        return """You are a research assistant that answers questions about company research reports.

**CRITICAL RULES:**
1. You MUST answer questions using ONLY the information provided in the report excerpts below.
2. If the information needed to answer the question is NOT in the provided excerpts, you MUST say "I don't know from this report" or "This information is not available in the report."
3. DO NOT use any knowledge outside of the provided report excerpts.
4. DO NOT make up information or infer details not explicitly stated in the excerpts.
5. When citing information, reference the section or context from the excerpts.

**Guidelines:**
- Be precise and accurate
- Cite specific information from the excerpts when possible
- If the question requires information from multiple sections, synthesize across the provided excerpts
- If excerpts are unclear or contradictory, note this in your answer
- Keep answers concise but complete
- If asked about something not in the excerpts, clearly state that the information is not available in this report

**Your goal:** Provide accurate, helpful answers based solely on the provided report excerpts."""
    
    def _build_rag_prompt(
        self,
        question: str,
        chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Build the RAG prompt with relevant chunks.
        
        Args:
            question: User's question
            chunks: Relevant chunks from vector search
            conversation_history: Previous conversation turns
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            "Relevant excerpts from the report:",
            ""
        ]
        
        # Add chunks with section context
        for i, chunk in enumerate(chunks, 1):
            section_info = f" (Section: {chunk.get('section', 'Unknown')})" if chunk.get('section') else ""
            prompt_parts.append(f"[Excerpt {i}{section_info}]")
            prompt_parts.append(chunk['chunk_text'])
            prompt_parts.append("")
        
        prompt_parts.append("---")
        prompt_parts.append("")
        
        # Add conversation history if available
        if conversation_history:
            prompt_parts.append("Previous conversation:")
            for turn in conversation_history[-3:]:  # Last 3 turns for context
                role = turn.get('role', 'user')
                content = turn.get('content', '')
                prompt_parts.append(f"{role.capitalize()}: {content}")
            prompt_parts.append("")
        
        prompt_parts.append(f"User question: {question}")
        prompt_parts.append("")
        prompt_parts.append("Answer the question using ONLY the information from the report excerpts above. If the information is not available in the excerpts, say so clearly.")
        
        return "\n".join(prompt_parts)
    
    def chat_with_report(
        self,
        report_id: str,
        question: str,
        reset_history: bool = False
    ) -> str:
        """
        Chat with a report, maintaining conversation history.
        
        Args:
            report_id: Report ID
            question: User's question
            reset_history: Whether to reset conversation history
        
        Returns:
            Agent's answer
        """
        if reset_history:
            self.conversation_history = []
        
        # Get answer
        answer = self.answer_question(
            report_id=report_id,
            user_question=question,
            conversation_history=self.conversation_history
        )
        
        # Update conversation history
        self.conversation_history.append({"role": "user", "content": question})
        self.conversation_history.append({"role": "assistant", "content": answer})
        
        return answer
    
    def reset_conversation(self):
        """Reset conversation history."""
        self.conversation_history = []

