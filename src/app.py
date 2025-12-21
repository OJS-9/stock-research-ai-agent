"""
Flask web interface for the Stock Research AI Agent.
"""

import sys
from pathlib import Path

# Add project root to Python path to allow imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask, render_template, request, redirect, url_for, session
import os
from dotenv import load_dotenv
import uuid

from agent import create_agent, StockResearchAgent

# Load environment variables
load_dotenv()

# Create Flask app
# Set template and static folders explicitly to point to project root
app = Flask(__name__, 
            template_folder=str(project_root / 'templates'), 
            static_folder=str(project_root / 'static'))
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())

# Global agent instances (keyed by session ID)
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
            raise ValueError(f"Failed to initialize agent: {str(e)}")
    return agent_sessions[session_id]


def get_or_create_session_id():
    """Get or create a session ID for the current user."""
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return session['session_id']


@app.route('/')
def index():
    """Render the main landing page."""
    # Initialize session ID if needed
    get_or_create_session_id()
    
    # Get current values from session for form pre-filling
    current_ticker = session.get('current_ticker', '')
    current_trade_type = session.get('current_trade_type', 'Investment')
    
    return render_template(
        'index.html',
        current_ticker=current_ticker,
        current_trade_type=current_trade_type
    )


@app.route('/chat')
def chat():
    """Render the chat interface."""
    # Initialize session ID if needed
    get_or_create_session_id()
    
    # Get conversation history from session
    conversation_history = session.get('conversation_history', [])
    current_ticker = session.get('current_ticker', '')
    current_trade_type = session.get('current_trade_type', 'Investment')
    
    return render_template(
        'chat.html',
        conversation_history=conversation_history,
        current_ticker=current_ticker,
        current_trade_type=current_trade_type
    )


@app.route('/start_research', methods=['POST'])
def start_research():
    """Handle form submission to start research."""
    ticker = request.form.get('ticker', '').strip()
    trade_type = request.form.get('trade_type', '')
    
    # Validate input
    if not ticker:
        session['status_message'] = '❌ Please enter a stock ticker.'
        return redirect(url_for('index'))
    
    if not trade_type:
        session['status_message'] = '❌ Please select a trade type.'
        return redirect(url_for('index'))
    
    ticker = ticker.upper()
    
    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        agent.reset_conversation()
        
        # Start research
        response = agent.start_research(ticker, trade_type)
        
        # Store conversation in session as list of message dicts
        conversation_history = [
            {"role": "assistant", "content": response}
        ]
        
        session['conversation_history'] = conversation_history
        session['current_ticker'] = ticker
        session['current_trade_type'] = trade_type
        session['status_message'] = f'✅ Research started for {ticker} ({trade_type})'
        
    except Exception as e:
        session['status_message'] = f'❌ Error: {str(e)}'
        session['conversation_history'] = []
    
    return redirect(url_for('chat'))


@app.route('/continue', methods=['POST'])
def continue_conversation():
    """Handle form submission to continue conversation."""
    user_input = request.form.get('user_response', '').strip()
    
    # Validate input
    if not user_input:
        session['status_message'] = '⚠️ Please enter a response.'
        return redirect(url_for('chat'))
    
    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        
        # Get agent response
        response = agent.continue_conversation(user_input)
        
        # Get current conversation history
        conversation_history = session.get('conversation_history', [])
        
        # Append user message and agent response
        conversation_history.append({"role": "user", "content": user_input})
        conversation_history.append({"role": "assistant", "content": response})
        
        # Update session
        session['conversation_history'] = conversation_history
        session['status_message'] = '✅ Response received'
        
    except Exception as e:
        session['status_message'] = f'❌ Error: {str(e)}'
    
    return redirect(url_for('chat'))


@app.route('/generate_report', methods=['POST'])
def generate_report():
    """Handle form submission to generate report after followup questions."""
    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        
        # Extract context from conversation history
        conversation_history = session.get('conversation_history', [])
        context = ""
        for msg in conversation_history:
            if msg.get('role') == 'user':
                context += f"User: {msg.get('content', '')}\n"
        
        # Generate report
        session['status_message'] = '🔄 Generating report... This may take a few minutes.'
        session['conversation_history'] = conversation_history  # Preserve history
        session.modified = True
        
        report_text = agent.generate_report(context=context)
        report_id = agent.current_report_id
        
        # Store report in session
        session['current_report_id'] = report_id
        session['report_text'] = report_text
        session['status_message'] = f'✅ Report generated successfully! Report ID: {report_id[:8]}...'
        
        # Add report to conversation
        conversation_history.append({
            "role": "assistant",
            "content": f"Report Generated:\n\n{report_text[:500]}...\n\n[Full report stored. You can now chat with it using the chat interface.]"
        })
        session['conversation_history'] = conversation_history
        
    except Exception as e:
        session['status_message'] = f'❌ Error generating report: {str(e)}'
    
    return redirect(url_for('index'))


@app.route('/chat_report', methods=['POST'])
def chat_report():
    """Handle form submission to chat with report."""
    question = request.form.get('chat_question', '').strip()
    
    # Validate input
    if not question:
        session['status_message'] = '⚠️ Please enter a question.'
        return redirect(url_for('index'))
    
    if 'current_report_id' not in session:
        session['status_message'] = '❌ No report available. Please generate a report first.'
        return redirect(url_for('index'))
    
    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        agent.current_report_id = session.get('current_report_id')
        
        # Get answer from chat agent
        answer = agent.chat_with_report(question)
        
        # Update chat history in session
        chat_history = session.get('chat_history', [])
        chat_history.append({"role": "user", "content": question})
        chat_history.append({"role": "assistant", "content": answer})
        session['chat_history'] = chat_history
        session['status_message'] = '✅ Answer received'
        
    except Exception as e:
        session['status_message'] = f'❌ Error: {str(e)}'
    
    return redirect(url_for('index'))


@app.route('/clear', methods=['POST'])
def clear_conversation():
    """Handle form submission to clear conversation."""
    session['conversation_history'] = []
    session['current_ticker'] = ''
    session['current_trade_type'] = 'Investment'
    session['status_message'] = 'Conversation cleared. Ready for new research.'
    
    # Optionally reset agent
    session_id = get_or_create_session_id()
    if session_id in agent_sessions:
        try:
            agent_sessions[session_id].reset_conversation()
        except:
            pass
    
    return redirect(url_for('chat'))


def main():
    """Main entry point for the Flask app."""
    # Check for required environment variables
    if not os.getenv("OPENAI_API_KEY"):
        print("Warning: OPENAI_API_KEY not found in environment variables.")
        print("Please set it in your .env file or environment.")
    
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=True
    )


if __name__ == "__main__":
    main()

