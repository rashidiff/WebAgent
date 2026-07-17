# Web Browser AI Agent 🌐🤖

A local, lightweight, and extremely fast Full-Stack Web Browser AI Agent built using a **Chrome Extension (Manifest V3)**, **FastAPI**, and **LangChain**.

The agent operates directly on the user's active, logged-in browser session. Instead of running a headless browser via heavy frameworks like Selenium or Playwright, the agent communicates with the Chrome Extension over a local WebSocket connection, executing actions directly via the browser's own scripting API.

---

## ✨ Features

- **In-Browser Execution**: Automates tasks directly on your active, logged-in tab (e.g. adding items to a cart, filling out forms, searching pages).
- **Gemini Minimalist UI**: A stunning, modern dark-themed chat interface matching the Google Gemini chat client layout.
- **Robust DOM Serialization**: Automatically parses webpage DOM structures, filtering out non-interactive elements and tagging interactive nodes with a temporary `data-agent-id` attribute to guarantee 100% targeting accuracy.
- **Modern LangChain Loop**: Uses a custom tool-calling loop with `llm.bind_tools` and standard message streams, making it fully compatible with LangChain v1.3.11+.
- **Multi-Model Support**: Pre-configured for **DeepSeek** (`deepseek-chat`), with seamless fallbacks to **Google Gemini** (`gemini-1.5-flash`), **OpenAI** (`gpt-4o-mini`), or **Anthropic** (`claude-3-5-sonnet-latest`).
- **Persistent History**: Every chat message and browser action is logged to a local SQLite database (`backend/agent_history.db`), retrievable via `GET /sessions` and `GET /sessions/{id}`.
- **Context-Aware Agent Loop**: Caps the number of DOM elements sent per step, collapses older DOM snapshots to keep context size bounded across long tasks, and remembers a short summary of prior tasks completed in the same session.

---

## 📂 Project Structure

```
browser-agent/
├── extension/             # Chrome Extension (Frontend UI & Execution)
│   ├── manifest.json      # MV3 configuration & permissions
│   ├── sidepanel.html     # Minimalist chat sidebar layout
│   ├── sidepanel.js       # WebSocket manager & page message broker
│   ├── sidepanel.css      # Custom dark-theme stylesheet
│   ├── background.js      # Service worker configuring side panel behavior
│   └── content.js         # Page script for DOM parsing & event dispatching
│
└── backend/               # FastAPI Backend (LangChain Brain)
    ├── main.py            # WebSocket server endpoint, routing & history API
    ├── agent.py           # LangChain tool binding & Custom Agent Loop
    ├── database.py        # SQLite persistence for sessions/messages/actions
    ├── requirements.txt   # Python package dependencies
    └── .env.example       # Template for required environment variables
```

---

## 🛠️ Installation & Setup

### 1. Run the FastAPI Backend

1. **Navigate to the backend folder**:
   ```bash
   cd browser-agent/backend
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your API keys**:
   Copy the provided template and fill in your values:
   ```bash
   cp .env.example .env
   ```
   ```env
   LLM_PROVIDER=deepseek
   LLM_MODEL_NAME=deepseek-chat
   LLM_MAX_TOKENS=2048
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   ```
   Only the API key matching your chosen `LLM_PROVIDER` (`gemini`, `openai`, `anthropic`, or `deepseek`) is required. See `.env.example` for the full list of supported variables.

5. **Start the server**:
   ```bash
   python main.py
   ```
   *The server starts listening on `http://127.0.0.1:8000`.*

---

### 2. Install the Chrome Extension

1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Toggle on **Developer mode** in the top-right corner.
3. Click **Load unpacked** in the top-left corner.
4. Select the `browser-agent/extension` folder.
5. Pin the **Web Browser AI Agent** extension to your toolbar.

---

## 🚀 How to Use

1. Go to any public website (e.g. `https://google.com` or `https://codeforces.com`).
2. Click the **Web Browser AI Agent** icon in your toolbar to open the sidebar.
3. Once the status shows **`Connected`** in green, type your instruction in the prompt box (e.g. `"Search for DeepSeek on Google"` or `"List the next Codeforces contests"`).
4. Click **Send** and watch the agent navigate, click, type, and summarize findings in real-time.

---

## 🗄️ Session History

Every WebSocket connection is logged as a session in `backend/agent_history.db` (SQLite, created automatically on first run). Two read-only endpoints expose it:

- `GET /sessions` — lists all sessions, most recent first.
- `GET /sessions/{session_id}` — returns the full list of chat messages and browser actions recorded for that session.
