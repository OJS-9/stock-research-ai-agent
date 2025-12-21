"""
Flask web interface for the Stock Research AI Agent.
"""

import sys
from pathlib import Path

# Add project root to Python path to allow imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
from dotenv import load_dotenv
import uuid
import markdown

from agent import create_agent, StockResearchAgent

# Load environment variables
load_dotenv()

# Create Flask app
# Set template and static folders explicitly to point to project root
app = Flask(__name__, 
            template_folder=str(project_root / 'templates'), 
            static_folder=str(project_root / 'static'))
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())

# Register markdown filter for Jinja2 templates
app.jinja_env.filters['markdown'] = markdown.markdown

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
    # #region agent log
    log_path = '/Users/orsalinas/projects/Stock Protfolio Agent/.cursor/debug.log'
    import json
    from datetime import datetime
    try:
        with open(log_path, 'a') as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": "C",
                "location": "app.py:continue_conversation:entry",
                "message": "Route entry - checking request data",
                "data": {
                    "form_keys": list(request.form.keys()),
                    "user_response_raw": request.form.get('user_response', 'NOT_FOUND'),
                    "is_ajax": request.headers.get('X-Requested-With') == 'XMLHttpRequest',
                    "content_type": request.content_type
                },
                "timestamp": int(datetime.now().timestamp() * 1000)
            }) + '\n')
    except: pass
    # #endregion
    
    user_input = request.form.get('user_response', '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # #region agent log
    try:
        with open(log_path, 'a') as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": "C",
                "location": "app.py:continue_conversation:after_get",
                "message": "After getting user_input",
                "data": {
                    "user_input": user_input,
                    "user_input_length": len(user_input),
                    "is_empty": not user_input
                },
                "timestamp": int(datetime.now().timestamp() * 1000)
            }) + '\n')
    except: pass
    # #endregion
    
    # Validate input
    if not user_input:
        # #region agent log
        try:
            with open(log_path, 'a') as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "C",
                    "location": "app.py:continue_conversation:validation_failed",
                    "message": "Validation failed - empty input",
                    "data": {
                        "is_ajax": is_ajax,
                        "user_input": user_input
                    },
                    "timestamp": int(datetime.now().timestamp() * 1000)
                }) + '\n')
        except: pass
        # #endregion
        if is_ajax:
            return jsonify({'success': False, 'error': '⚠️ Please enter a response.'}), 400
        session['status_message'] = '⚠️ Please enter a response.'
        return redirect(url_for('chat'))
    
    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        
        # Store previous report_id to detect if a new report was generated
        previous_report_id = session.get('current_report_id')
        
        # Get agent response
        response = agent.continue_conversation(user_input)
        
        # Get current conversation history
        conversation_history = session.get('conversation_history', [])
        
        # Append user message and agent response
        conversation_history.append({"role": "user", "content": user_input})
        conversation_history.append({"role": "assistant", "content": response})
        
        # Check if a report was generated during this conversation turn
        current_report_id = agent.current_report_id
        report_generated = False
        report_preview = None
        
        if current_report_id and current_report_id != previous_report_id:
            # A new report was generated - get full report text from agent and add to conversation
            report_text = getattr(agent, 'last_report_text', None) or session.get('report_text', '')
            if report_text:
                # Display the full report text in the chat
                report_preview = f"# Research Report\n\n{report_text}"
                conversation_history.append({
                    "role": "assistant",
                    "content": report_preview
                })
                session['current_report_id'] = current_report_id
                session['report_text'] = report_text
                report_generated = True
        
        # Update session
        session['conversation_history'] = conversation_history
        session['status_message'] = '✅ Response received'
        
        # If AJAX request, return JSON
        if is_ajax:
            return jsonify({
                'success': True,
                'user_message': user_input,
                'assistant_message': response,
                'conversation_history': conversation_history,
                'report_generated': report_generated,
                'report_preview': report_preview
            })
        
    except Exception as e:
        if is_ajax:
            return jsonify({'success': False, 'error': f'❌ Error: {str(e)}'}), 500
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
        
        # Add full report to conversation
        report_preview = f"# Research Report\n\n{report_text}"
        conversation_history.append({
            "role": "assistant",
            "content": report_preview
        })
        session['conversation_history'] = conversation_history
        
    except Exception as e:
        session['status_message'] = f'❌ Error generating report: {str(e)}'
    
    return redirect(url_for('chat'))


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

