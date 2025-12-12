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

from src.agent import create_agent, StockResearchAgent

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
            5. Review the generated research report
            """
        )
        
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
        
        # Hidden session ID
        session_id = gr.State(value=default_session_id)
        
        # Event handlers
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
        
        # Allow Enter key to submit
        user_response.submit(
            fn=continue_conversation,
            inputs=[user_response, conversation, session_id],
            outputs=[conversation, status_display]
        ).then(
            fn=lambda: "",
            outputs=[user_response]
        )
        
        clear_btn.click(
            fn=lambda: ("", "Conversation cleared. Ready for new research."),
            outputs=[conversation, status_display]
        )
        
        gr.Markdown(
            """
            ---
            **Note:** This agent uses Alpha Vantage MCP server for financial data. 
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

