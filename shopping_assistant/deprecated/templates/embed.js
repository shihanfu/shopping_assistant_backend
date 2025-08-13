// Shopping Assistant Embed Script
// This script creates a floating chat widget on the shopping website

(function() {
    'use strict';

    // Configuration
    const CHAT_SERVER_URL = 'http://localhost:5000'; // Change this to your chat server URL
    const WIDGET_ID = 'shopping-assistant-widget';
    
    // Create widget styles
    const widgetStyles = `
        #${WIDGET_ID} {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 10000;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        #${WIDGET_ID} .chat-toggle {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        #${WIDGET_ID} .chat-toggle:hover {
            transform: scale(1.1);
            box-shadow: 0 6px 16px rgba(0,0,0,0.2);
        }
        
        #${WIDGET_ID} .chat-container {
            position: absolute;
            bottom: 80px;
            right: 0;
            width: 350px;
            height: 500px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            display: none;
            flex-direction: column;
            overflow: hidden;
        }
        
        #${WIDGET_ID} .chat-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px;
            text-align: center;
            position: relative;
        }
        
        #${WIDGET_ID} .chat-header h3 {
            margin: 0;
            font-size: 16px;
            font-weight: 600;
        }
        
        #${WIDGET_ID} .chat-header p {
            margin: 5px 0 0 0;
            font-size: 12px;
            opacity: 0.9;
        }
        
        #${WIDGET_ID} .close-button {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        #${WIDGET_ID} .close-button:hover {
            background: rgba(255,255,255,0.3);
        }
        
        #${WIDGET_ID} .chat-messages {
            flex: 1;
            padding: 15px;
            overflow-y: auto;
            background: #f8f9fa;
            max-height: 300px;
        }
        
        #${WIDGET_ID} .message {
            margin-bottom: 10px;
            display: flex;
            align-items: flex-start;
        }
        
        #${WIDGET_ID} .message.user {
            justify-content: flex-end;
        }
        
        #${WIDGET_ID} .message.assistant {
            justify-content: flex-start;
        }
        
        #${WIDGET_ID} .message-content {
            max-width: 80%;
            padding: 10px 15px;
            border-radius: 15px;
            word-wrap: break-word;
            line-height: 1.3;
            font-size: 14px;
        }
        
        #${WIDGET_ID} .message.user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 5px;
        }
        
        #${WIDGET_ID} .message.assistant .message-content {
            background: white;
            color: #333;
            border: 1px solid #e0e0e0;
            border-bottom-left-radius: 5px;
        }
        
        #${WIDGET_ID} .message-avatar {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            margin: 0 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: white;
            font-size: 12px;
        }
        
        #${WIDGET_ID} .message.user .message-avatar {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        
        #${WIDGET_ID} .message.assistant .message-avatar {
            background: #28a745;
        }
        
        #${WIDGET_ID} .chat-input-container {
            padding: 15px;
            background: white;
            border-top: 1px solid #e0e0e0;
        }
        
        #${WIDGET_ID} .chat-input-wrapper {
            display: flex;
            gap: 8px;
            align-items: flex-end;
        }
        
        #${WIDGET_ID} .chat-input {
            flex: 1;
            padding: 10px 15px;
            border: 2px solid #e0e0e0;
            border-radius: 20px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.3s ease;
            resize: none;
            min-height: 40px;
            max-height: 80px;
        }
        
        #${WIDGET_ID} .chat-input:focus {
            border-color: #667eea;
        }
        
        #${WIDGET_ID} .send-button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s ease;
        }
        
        #${WIDGET_ID} .send-button:hover {
            transform: scale(1.05);
        }
        
        #${WIDGET_ID} .send-button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        #${WIDGET_ID} .send-button svg {
            width: 16px;
            height: 16px;
        }
        
        #${WIDGET_ID} .typing-indicator {
            display: none;
            padding: 10px 15px;
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 15px;
            border-bottom-left-radius: 5px;
            margin-bottom: 10px;
            max-width: 80%;
        }
        
        #${WIDGET_ID} .typing-dots {
            display: flex;
            gap: 3px;
        }
        
        #${WIDGET_ID} .typing-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #999;
            animation: typing 1.4s infinite ease-in-out;
        }
        
        #${WIDGET_ID} .typing-dot:nth-child(1) { animation-delay: -0.32s; }
        #${WIDGET_ID} .typing-dot:nth-child(2) { animation-delay: -0.16s; }
        
        @keyframes typing {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }
        
        #${WIDGET_ID} .welcome-message {
            text-align: center;
            color: #666;
            font-style: italic;
            margin: 10px 0;
            font-size: 13px;
        }
        
        @media (max-width: 768px) {
            #${WIDGET_ID} .chat-container {
                width: 300px;
                height: 400px;
            }
            
            #${WIDGET_ID} {
                bottom: 10px;
                right: 10px;
            }
        }
    `;

    // Create widget HTML
    function createWidget() {
        const widget = document.createElement('div');
        widget.id = WIDGET_ID;
        widget.innerHTML = `
            <button class="chat-toggle" onclick="toggleChat()">🛍️</button>
            <div class="chat-container">
                <div class="chat-header">
                    <button class="close-button" onclick="toggleChat()">×</button>
                    <h3>🛍️ Rufus - Shopping Assistant</h3>
                    <p>I can help you search for products!</p>
                </div>
                <div class="chat-messages">
                    <div class="welcome-message">
                        👋 Hi! I'm Rufus, your friendly shopping assistant. What would you like to find today?
                    </div>
                </div>
                <div class="typing-indicator">
                    <div class="typing-dots">
                        <div class="typing-dot"></div>
                        <div class="typing-dot"></div>
                        <div class="typing-dot"></div>
                    </div>
                </div>
                <div class="chat-input-container">
                    <div class="chat-input-wrapper">
                        <textarea class="chat-input" placeholder="Ask me to search for products..." rows="1"></textarea>
                        <button class="send-button" onclick="sendMessage()">
                            <svg fill="currentColor" viewBox="0 0 20 20">
                                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
        return widget;
    }

    // Add styles to document
    function addStyles() {
        const style = document.createElement('style');
        style.textContent = widgetStyles;
        document.head.appendChild(style);
    }

    // Widget state
    let isOpen = false;
    let conversationHistory = [];

    // Toggle chat visibility
    window.toggleChat = function() {
        const container = document.querySelector(`#${WIDGET_ID} .chat-container`);
        isOpen = !isOpen;
        container.style.display = isOpen ? 'flex' : 'none';
        
        if (isOpen) {
            const input = container.querySelector('.chat-input');
            input.focus();
        }
    };

    // Add message to chat
    function addMessage(content, isUser = false) {
        const messagesContainer = document.querySelector(`#${WIDGET_ID} .chat-messages`);
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${isUser ? 'user' : 'assistant'}`;
        
        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.textContent = isUser ? 'U' : 'R';
        
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        messageContent.textContent = content;
        
        messageDiv.appendChild(avatar);
        messageDiv.appendChild(messageContent);
        
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // Show/hide typing indicator
    function showTypingIndicator() {
        const indicator = document.querySelector(`#${WIDGET_ID} .typing-indicator`);
        indicator.style.display = 'block';
        const messagesContainer = document.querySelector(`#${WIDGET_ID} .chat-messages`);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function hideTypingIndicator() {
        const indicator = document.querySelector(`#${WIDGET_ID} .typing-indicator`);
        indicator.style.display = 'none';
    }

    // Send message
    window.sendMessage = async function() {
        const input = document.querySelector(`#${WIDGET_ID} .chat-input`);
        const sendButton = document.querySelector(`#${WIDGET_ID} .send-button`);
        const message = input.value.trim();
        
        if (!message) return;

        // Add user message
        addMessage(message, true);
        input.value = '';
        input.style.height = 'auto';

        // Disable input while processing
        sendButton.disabled = true;
        input.disabled = true;

        // Show typing indicator
        showTypingIndicator();

        try {
            const response = await fetch(`${CHAT_SERVER_URL}/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message })
            });

            const data = await response.json();

            if (response.ok) {
                hideTypingIndicator();
                addMessage(data.response);
            } else {
                hideTypingIndicator();
                addMessage('Sorry, I encountered an error. Please try again.');
            }
        } catch (error) {
            hideTypingIndicator();
            addMessage('Sorry, I encountered an error. Please try again.');
            console.error('Error:', error);
        } finally {
            // Re-enable input
            sendButton.disabled = false;
            input.disabled = false;
            input.focus();
        }
    };

    // Handle Enter key in input
    function setupInputHandlers() {
        const input = document.querySelector(`#${WIDGET_ID} .chat-input`);
        
        // Auto-resize textarea
        input.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 80) + 'px';
        });

        // Send message on Enter (but allow Shift+Enter for new line)
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // Initialize widget
    function init() {
        // Add styles
        addStyles();
        
        // Create and add widget
        const widget = createWidget();
        document.body.appendChild(widget);
        
        // Setup input handlers
        setupInputHandlers();
        
        console.log('Shopping Assistant widget loaded successfully!');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})(); 