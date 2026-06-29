# Web Browser AI Agent 🌐🤖

A local, lightweight, and extremely fast Full-Stack Web Browser AI Agent built using a **Chrome Extension (Manifest V3)**, **FastAPI**, and **LangChain**.

The agent operates directly on the user's active, logged-in browser session. Instead of running a headless browser via heavy frameworks like Selenium or Playwright, the agent communicates with the Chrome Extension over a local WebSocket connection, executing actions directly via the browser's own scripting API.

---

## ✨ Features

- **In-Browser Execution**: Automates tasks directly on your active, logged-in tab (e.g. adding items to a cart, filling out forms, searching pages).
- **Gemini Minimalist UI**: A stunning, modern dark-themed chat interface matching the Google Gemini chat client layout.
- **Robust DOM Serialization**: Automatically parses webpage DOM structures, filtering out non-interactive elements and tagging interactive nodes with a temporary `data-agent-id` attribute to guarantee 100% targeting accuracy.
- **Modern LangChain Loop**: Uses a custom tool-calling loop with `llm.bind_tools` and standard message streams, making it fully compatible with LangChain v1.3.11+.
- **Multi-Model Support**: Pre-configured for **DeepSeek** (`deepseek-chat`), with seamless fallbacks to **Google Gemini** (`gemini-1.5-flash`) or **OpenAI** (`gpt-4o-mini`).

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
    ├── main.py            # WebSocket server endpoint & routing
    ├── agent.py           # LangChain tool binding & Custom Agent Loop
    └── requirements.txt   # Python package dependencies
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
   Create a `.env` file in the `backend/` directory:
   ```env
   LLM_PROVIDER=deepseek
   LLM_MODEL_NAME=deepseek-chat
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   ```

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
