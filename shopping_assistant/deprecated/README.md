# 🛍️ Shopping Assistant Chat Widget

A floating chat assistant that can be embedded on the shopping website at `http://metis.lti.cs.cmu.edu:7770/`. The assistant uses AWS Bedrock and WebAgentEnv to provide intelligent shopping assistance.

## Features

- 🛍️ **Floating Chat Widget**: Beautiful, responsive chat interface that appears on the shopping website
- 🤖 **AI-Powered Assistant**: Uses Claude 3 Sonnet via AWS Bedrock for intelligent responses
- 🔍 **Product Search**: Can search for products and visit product pages using WebAgentEnv
- 📱 **Responsive Design**: Works seamlessly on desktop and mobile devices
- 🎨 **Modern UI**: Beautiful gradient design with smooth animations
- ⚡ **Real-time**: Instant responses with typing indicators

## Quick Start

### 1. Start the Chat Server

```bash
# Navigate to the project root
cd /path/to/rl_web_agent

# Start the chat server
python shopping_assistant/run_chat_server.py
```

The server will start on `http://localhost:5000`

### 2. Inject the Widget

Open the shopping website: http://metis.lti.cs.cmu.edu:7770/

Then inject the widget using one of these methods:

#### Method 1: Browser Console
1. Open browser developer tools (F12)
2. Go to the Console tab
3. Run this code:
```javascript
const script = document.createElement('script');
script.src = 'http://localhost:5000/embed.js';
document.head.appendChild(script);
```

#### Method 2: Bookmarklet
1. Drag this link to your bookmarks bar:
   [🛍️ Chat Assistant](javascript:(function(){const script=document.createElement('script');script.src='http://localhost:5000/embed.js';document.head.appendChild(script);})();)
2. Click the bookmark when on the shopping website

#### Method 3: Use the Injection Tool
1. Open `shopping_assistant/inject_chat.html` in your browser
2. Follow the instructions on the page

### 3. Start Chatting!

1. Look for the shopping bag icon (🛍️) in the bottom-right corner
2. Click it to open the chat interface
3. Start chatting with Rufus, your shopping assistant!

## Architecture

```
shopping_assistant/
├── chat_app.py              # Flask web server
├── converse.py              # Core conversation logic
├── prompts/
│   └── system_prompt.py     # AI assistant prompt
├── tool_config.py           # Tool definitions
├── templates/
│   ├── chat_interface.html  # Main chat interface
│   └── embed.js            # Widget injection script
├── inject_chat.html         # Injection helper tool
└── run_chat_server.py      # Server launcher
```

## Components

### Chat Server (`chat_app.py`)
- Flask web server that handles chat requests
- Integrates with AWS Bedrock for AI responses
- Manages conversation history
- Provides REST API endpoints

### WebAgentEnv Integration (`converse.py`)
- Uses the existing WebAgentEnv for browser automation
- Provides `search` and `visit_product` tools
- Handles async tool execution

### Chat Widget (`embed.js`)
- Self-contained JavaScript widget
- Creates floating chat interface
- Handles real-time communication with server
- Responsive design for all devices

## API Endpoints

- `GET /` - Main chat interface
- `POST /chat` - Send/receive messages
- `POST /reset` - Reset conversation
- `GET /health` - Health check
- `GET /embed.js` - Widget script
- `GET /embed.css` - Widget styles

## Configuration

### AWS Bedrock
The assistant uses AWS Bedrock with Claude 3 Sonnet. Make sure you have:
- AWS credentials configured
- Access to Bedrock service
- Proper IAM permissions

### WebAgentEnv
The assistant uses the existing WebAgentEnv configuration from `rl_web_agent/conf/config.yaml`.

## Troubleshooting

### Server Won't Start
- Check if port 5000 is available
- Ensure all dependencies are installed
- Check AWS credentials are configured

### Widget Not Appearing
- Verify the chat server is running on `http://localhost:5000`
- Check browser console for errors
- Ensure CORS is not blocking the request

### Chat Not Working
- Check if AWS Bedrock is accessible
- Verify WebAgentEnv is properly configured
- Check server logs for errors

## Development

### Adding New Tools
1. Add tool definition to `tool_config.py`
2. Implement tool function in `converse.py`
3. Update system prompt in `prompts/system_prompt.py`

### Customizing the Widget
1. Modify `templates/embed.js` for widget behavior
2. Update styles in the same file
3. Test on the shopping website

### Styling Changes
The widget uses inline CSS for portability. Modify the `widgetStyles` variable in `embed.js` to change appearance.

## Security Notes

- The widget communicates with `localhost:5000` - ensure this is secure in production
- AWS credentials should be properly configured and secured
- Consider HTTPS for production deployment

## Dependencies

- Flask
- boto3 (AWS SDK)
- asyncio
- WebAgentEnv (from the main project)

## License

This project follows the same license as the main RL Web Agent project. 