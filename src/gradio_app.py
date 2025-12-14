"""
Gradio interface for the Stock Research AI Agent.
"""

import sys
from pathlib import Path

# Add project root to Python path to allow imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import gradio as gr
from typing import Tuple, Optional
import os
from dotenv import load_dotenv

from agent import create_agent, StockResearchAgent

# Load environment variables
load_dotenv()

# Global agent instance (will be created per session)
agent_sessions = {}


def initialize_session(session_id: str) -> StockResearchAgent:
    """
    Initialize or get agent for a session.
    
    Args:
        session_id: Unique session identifier
    
    Returns:
        StockResearchAgent instance
    """
    if session_id not in agent_sessions:
        try:
            agent_sessions[session_id] = create_agent()
        except Exception as e:
            raise gr.Error(f"Failed to initialize agent: {str(e)}")
    return agent_sessions[session_id]


def start_research(ticker: str, trade_type: str, session_id: str) -> Tuple[str, str]:
    """
    Start a research session.
    
    Args:
        ticker: Stock ticker symbol
        trade_type: Type of trade
        session_id: Session identifier
    
    Returns:
        Tuple of (conversation_history, status_message)
    """
    if not ticker or not ticker.strip():
        return "", "❌ Please enter a stock ticker."
    
    if not trade_type:
        return "", "❌ Please select a trade type."
    
    ticker = ticker.strip().upper()
    
    try:
        agent = initialize_session(session_id)
        agent.reset_conversation()
        
        # Start research
        response = agent.start_research(ticker, trade_type)
        
        conversation = f"**Agent:** {response}\n\n"
        status = f"✅ Research started for {ticker} ({trade_type})"
        
        return conversation, status
        
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        return "", error_msg


def continue_conversation(user_input: str, conversation_history: str, session_id: str) -> Tuple[str, str]:
    """
    Continue conversation with user input.
    
    Args:
        user_input: User's response
        conversation_history: Current conversation history
        session_id: Session identifier
    
    Returns:
        Tuple of (updated_conversation_history, status_message)
    """
    if not user_input or not user_input.strip():
        return conversation_history, "⚠️ Please enter a response."
    
    try:
        agent = initialize_session(session_id)
        
        # Get agent response
        response = agent.continue_conversation(user_input.strip())
        
        # Update conversation history
        updated_history = conversation_history
        if updated_history:
            updated_history += "\n\n"
        updated_history += f"**You:** {user_input}\n\n"
        updated_history += f"**Agent:** {response}\n\n"
        
        status = "✅ Response received"
        
        return updated_history, status
        
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        return conversation_history, error_msg


def generate_report(conversation_history: str, session_id: str) -> Tuple[str, str, str]:
    """
    Generate research report after followup questions.
    
    Args:
        conversation_history: Current conversation history
        session_id: Session identifier
    
    Returns:
        Tuple of (updated_conversation_history, status_message, report_id)
    """
    try:
        agent = initialize_session(session_id)
        
        # Extract context from conversation
        context = conversation_history if conversation_history else ""
        
        # Generate report
        report_text = agent.generate_report(context=context)
        report_id = agent.current_report_id or "unknown"
        
        # Update conversation history
        updated_history = conversation_history
        if updated_history:
            updated_history += "\n\n"
        updated_history += f"**Report Generated:**\n\n{report_text[:1000]}...\n\n[Full report stored. You can now chat with it in the Chat tab.]\n\n"
        
        status = f"✅ Report generated successfully! Report ID: {report_id[:8]}..."
        
        return updated_history, status, report_id
        
    except Exception as e:
        error_msg = f"❌ Error generating report: {str(e)}"
        return conversation_history, error_msg, ""


def chat_with_report(question: str, chat_history: str, report_id: str, session_id: str) -> Tuple[str, str]:
    """
    Chat with a generated report.
    
    Args:
        question: User's question
        chat_history: Current chat history
        report_id: Report ID
        session_id: Session identifier
    
    Returns:
        Tuple of (updated_chat_history, status_message)
    """
    if not question or not question.strip():
        return chat_history, "⚠️ Please enter a question."
    
    if not report_id or report_id == "unknown":
        return chat_history, "❌ No report available. Please generate a report first."
    
    try:
        agent = initialize_session(session_id)
        agent.current_report_id = report_id
        
        # Get answer
        answer = agent.chat_with_report(question.strip())
        
        # Update chat history
        updated_history = chat_history
        if updated_history:
            updated_history += "\n\n"
        updated_history += f"**You:** {question}\n\n"
        updated_history += f"**Agent:** {answer}\n\n"
        
        status = "✅ Answer received"
        
        return updated_history, status
        
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        return chat_history, error_msg


def create_interface():
    """Create and configure the Gradio interface."""
    
    # Generate a unique session ID (in production, use proper session management)
    import uuid
    default_session_id = str(uuid.uuid4())
    
    with gr.Blocks(title="Stock Research AI Agent") as demo:
        gr.Markdown(
            """
            # 📊 Stock Research AI Agent
            
            Get comprehensive fundamental research reports for stocks based on your trading strategy.
            
            **How to use:**
            1. Enter a stock ticker (e.g., AAPL, TSLA, MSFT)
            2. Select your trade type (Day Trade, Swing Trade, or Investment)
            3. Click "Start Research" to begin
            4. Answer any follow-up questions the agent asks
            5. Click "Generate Report" to create the research report
            6. Use the Chat tab to ask questions about the generated report
            """
        )
        
        with gr.Tabs() as tabs:
            with gr.Tab("Generate Report"):
                with gr.Row():
                    with gr.Column(scale=1):
                        ticker_input = gr.Textbox(
                            label="Stock Ticker",
                            placeholder="e.g., AAPL, TSLA, MSFT",
                            value=""
                        )
                        
                        trade_type = gr.Radio(
                            choices=["Day Trade", "Swing Trade", "Investment"],
                            label="Trade Type",
                            value="Investment"
                        )
                        
                        start_btn = gr.Button("Start Research", variant="primary", size="lg")
                        generate_btn = gr.Button("Generate Report", variant="primary", size="lg")
                        
                        status_display = gr.Textbox(
                            label="Status",
                            interactive=False,
                            value="Ready to start research..."
                        )
                    
                    with gr.Column(scale=2):
                        conversation = gr.Textbox(
                            label="Conversation",
                            lines=20,
                            interactive=False,
                            placeholder="Conversation will appear here..."
                        )
                        
                        user_response = gr.Textbox(
                            label="Your Response",
                            placeholder="Type your response here...",
                            lines=3
                        )
                        
                        continue_btn = gr.Button("Send Response", variant="secondary")
                        clear_btn = gr.Button("Clear Conversation", variant="stop")
                
                # Hidden session ID and report ID
                session_id = gr.State(value=default_session_id)
                report_id_state = gr.State(value="")
            
            with gr.Tab("Chat with Report"):
                gr.Markdown("Ask questions about your generated report. The agent will answer based only on the report content.")
                
                report_id_display = gr.Textbox(
                    label="Report ID",
                    interactive=False,
                    value="No report generated yet"
                )
                
                chat_history = gr.Textbox(
                    label="Chat History",
                    lines=15,
                    interactive=False,
                    placeholder="Chat history will appear here..."
                )
                
                chat_question = gr.Textbox(
                    label="Your Question",
                    placeholder="Ask a question about the report...",
                    lines=3
                )
                
                chat_btn = gr.Button("Ask Question", variant="primary")
                clear_chat_btn = gr.Button("Clear Chat", variant="stop")
        
        # Event handlers for Generate Report tab
        start_btn.click(
            fn=start_research,
            inputs=[ticker_input, trade_type, session_id],
            outputs=[conversation, status_display]
        )
        
        continue_btn.click(
            fn=continue_conversation,
            inputs=[user_response, conversation, session_id],
            outputs=[conversation, status_display]
        ).then(
            fn=lambda: "",  # Clear user input after sending
            outputs=[user_response]
        )
        
        user_response.submit(
            fn=continue_conversation,
            inputs=[user_response, conversation, session_id],
            outputs=[conversation, status_display]
        ).then(
            fn=lambda: "",
            outputs=[user_response]
        )
        
        generate_btn.click(
            fn=generate_report,
            inputs=[conversation, session_id],
            outputs=[conversation, status_display, report_id_state]
        ).then(
            fn=lambda rid: rid if rid else "No report generated yet",
            inputs=[report_id_state],
            outputs=[report_id_display]
        )
        
        clear_btn.click(
            fn=lambda: ("", "Conversation cleared. Ready for new research.", ""),
            outputs=[conversation, status_display, report_id_state]
        ).then(
            fn=lambda: "No report generated yet",
            outputs=[report_id_display]
        )
        
        # Event handlers for Chat tab
        chat_btn.click(
            fn=chat_with_report,
            inputs=[chat_question, chat_history, report_id_state, session_id],
            outputs=[chat_history, status_display]
        ).then(
            fn=lambda: "",
            outputs=[chat_question]
        )
        
        chat_question.submit(
            fn=chat_with_report,
            inputs=[chat_question, chat_history, report_id_state, session_id],
            outputs=[chat_history, status_display]
        ).then(
            fn=lambda: "",
            outputs=[chat_question]
        )
        
        clear_chat_btn.click(
            fn=lambda: ("", "Chat cleared."),
            outputs=[chat_history, status_display]
        )
        
        gr.Markdown(
            """
            ---
            **Note:** This agent uses Alpha Vantage MCP server for financial data and OpenAI for embeddings. 
            Make sure you have configured your API keys in `.env` and `mcp.json` files.
            """
        )
    
    return demo


def main():
    """Main entry point for the Gradio app."""
    # Check for required environment variables
    if not os.getenv("OPENAI_API_KEY"):
        print("Warning: OPENAI_API_KEY not found in environment variables.")
        print("Please set it in your .env file or environment.")
    
    demo = create_interface()
    demo.launch(
        share=False,
        server_name="127.0.0.1",  # Use localhost for local access
        server_port=7860,
        theme=gr.themes.Soft(),  # Moved here for Gradio 6.0+
        show_error=True
    )


if __name__ == "__main__":
    main()

