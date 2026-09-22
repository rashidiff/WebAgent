# Web Browser AI Agent 🌐🤖

A local, lightweight, and extremely fast Full-Stack Web Browser AI Agent built using a **Chrome Extension (Manifest V3)**, **FastAPI**, and **LangChain**.

The agent operates directly on the user's active, logged-in browser session. Instead of running a headless browser via heavy frameworks like Selenium or Playwright, the agent communicates with the Chrome Extension over a local WebSocket connection, executing actions directly via the browser's own scripting API.

---

## ✨ Features

- **In-Browser Execution**: Automates tasks directly on your active, logged-in tab (e.g. adding items to a cart, filling out forms, searching pages).
- **Gemini Minimalist UI**: A stunning, modern dark-themed chat interface matching the Google Gemini chat client layout.
- **Robust DOM Serialization**: Automatically parses webpage DOM structures, including nested open shadow roots and same-origin frames, filtering out non-interactive elements and tagging interactive nodes with a temporary `data-agent-id` attribute for reliable targeting.
- **Modern LangChain Loop**: Uses a custom tool-calling loop with `llm.bind_tools` and standard message streams, validated against the pinned LangChain 1.3 dependency line in `backend/constraints.txt`.
- **Multi-Model Support**: Pre-configured for **DeepSeek** (`deepseek-chat`), with seamless fallbacks to **Google Gemini** (`gemini-1.5-flash`), **OpenAI** (`gpt-4o-mini`), or **Anthropic** (`claude-3-5-sonnet-latest`).
- **Persistent History**: Every chat message and browser action is logged to a local SQLite database (`backend/agent_history.db`), retrievable via `GET /sessions` and `GET /sessions/{id}`.
- **Context-Aware Agent Loop**: Caps the number of DOM elements sent per step, collapses older DOM snapshots to keep context size bounded across long tasks, and remembers a short summary of prior tasks completed in the same session.
- **Step-Budgeted Execution**: `MAX_AGENT_STEPS` limits executed browser actions, not just model reasoning turns, so multi-action runs stop at a predictable budget.
- **Replay & Screenshot Grounding**: Every agent run gets a durable replay timeline with plan/action/verify/self-check events, DOM summaries, and before/after screenshots exportable as Markdown or HTML.
- **Expanded Browser Tools**: Supports navigation, keyboard events, select controls, hover menus, browser history, reload, wait steps, visible page-text reads, clear/double-click/toggle, wait-for-text, modal/download detection, paste, and basic drag/drop.
- **Risk-Based Approval Guard**: Browser actions are classified as `read_only`, `low`, `medium`, `high`, or `critical`; high-risk actions such as submit, delete, checkout, payment, credential-like input, and account changes require explicit sidepanel approval by default.
- **Workflow Library**: Save successful replays as lightweight reusable workflows, reload them into the prompt box, and adjust parameters before running again.
- **Sidepanel Replay, Workflow & History Viewer**: Configure the backend URL and optional auth token, inspect sessions/replays/workflows, search local records, export replays, and clear local history without leaving the sidepanel.
- **Local Evaluation Suite**: Ships with mock form, shop, table, modal, and fake-login pages plus a smoke-test runner that emits JSON/Markdown-ready benchmark summaries.
- **Sensitive Value Redaction**: Credential-like input values are redacted before browser actions are written to local history.
- **Optional Local Auth**: Set `AGENT_AUTH_TOKEN` to protect the WebSocket and session history endpoints on shared machines.

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
├── backend/               # FastAPI Backend (LangChain Brain)
    ├── main.py            # WebSocket server endpoint, routing & history API
    ├── agent.py           # LangChain tool binding & Custom Agent Loop
    ├── database.py        # SQLite persistence for sessions/messages/actions
    ├── evaluation.py      # Local mock-page evaluation smoke runner
    ├── requirements.txt   # Python package dependencies
    └── .env.example       # Template for required environment variables
└── evals/mock_pages/      # Static benchmark pages for local evaluation
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
   pip install -r requirements.txt -c constraints.txt
   ```
   The backend now ships with pinned dependency versions in `constraints.txt` and mirrored project metadata in the repo-level `pyproject.toml`.

4. **Configure your API keys**:
   Copy the provided template and fill in your values:
   ```bash
   cp .env.example .env
   ```
   ```env
   LLM_PROVIDER=deepseek
   LLM_MODEL_NAME=deepseek-chat
   LLM_MAX_TOKENS=2048
   MAX_AGENT_STEPS=15
   ACTION_TIMEOUT_SECONDS=40
   MAX_DOM_ELEMENTS=150
   REQUIRE_ACTION_APPROVAL=true
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   ```
   Only the API key matching your chosen `LLM_PROVIDER` (`gemini`, `openai`, `anthropic`, or `deepseek`) is required. `MAX_AGENT_STEPS` counts executed browser tool calls, and `MAX_DOM_ELEMENTS=150` should stay aligned with the extension-side DOM capture budget. See `.env.example` for the full list of supported variables.

5. **Start the server**:
   ```bash
   python -m backend.main
   ```
   *The server starts listening on `http://127.0.0.1:8000`.*

---

### 2. Install the Chrome Extension

1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Toggle on **Developer mode** in the top-right corner.
3. Click **Load unpacked** in the top-left corner.
4. Select the `browser-agent/extension` folder.
5. Pin the **Web Browser AI Agent** extension to your toolbar.

The extension uses `activeTab`, `scripting`, `tabs`, and broad host access so it can keep acting after agent-driven cross-origin navigation in the active tab. Sensitive actions are still gated by the local approval guard.
The current extension defaults wait about `900ms` after tab-level navigation and `700ms` after DOM actions before re-reading the page state.

---

## 🚀 How to Use

1. Go to any public website (e.g. `https://google.com` or `https://codeforces.com`).
2. Click the **Web Browser AI Agent** icon in your toolbar to open the sidebar.
3. Once the status shows **`Connected`** in green, type your instruction in the prompt box (e.g. `"Search for DeepSeek on Google"` or `"List the next Codeforces contests"`).
4. Click **Send** and watch the agent navigate, click, type, and summarize findings in real-time.

---

## 🗄️ Session History

Every WebSocket connection is logged as a session in `backend/agent_history.db` (SQLite, created automatically on first run, or `AGENT_DB_PATH` if set). Sensitive input values are redacted before they are written to the action log. Replays, workflows, and eval results are stored in the same local database.

- `GET /sessions?limit=20&offset=0` — lists sessions with pagination metadata.
- `GET /sessions/{session_id}` — returns the full list of chat messages and browser actions recorded for that session.
- `DELETE /sessions/{session_id}` — deletes one recorded session.
- `DELETE /sessions` — clears all recorded session history.
- `GET /runs` / `GET /runs/{run_id}` — lists and opens replay timelines.
- `GET /runs/{run_id}/export?format=html|markdown` — exports a replay for sharing or debugging.
- `GET /workflows` / `POST /workflows` / `POST /workflows/{id}/run` / `DELETE /workflows/{id}` — manages saved workflow templates.
- `POST /evals/run` / `GET /evals` — runs and lists local evaluation smoke results.

If `AGENT_AUTH_TOKEN` is set, pass it as `X-Agent-Token` or configure the same token in the sidepanel settings.

---

## 🧪 Local Evaluation

Run the built-in smoke suite against static mock pages:

```bash
python -m backend.evaluation
```

Optional machine-readable outputs:

```bash
python -m backend.evaluation --json eval-results.json --markdown eval-summary.md
```

The V1 suite validates that the demo pages used for form filling, cart actions, table search, modal detection, and credential-field safety contain the expected markers. It is intentionally lightweight so it can run in CI and act as a foundation for future end-to-end browser-agent benchmarks.

---

## ✅ Development Checks

```bash
python -m py_compile backend/main.py backend/agent.py backend/database.py backend/settings.py backend/schemas.py backend/evaluation.py
python -m unittest discover backend/tests
python -m backend.evaluation
node --check extension/content.js
node --check extension/sidepanel.js
```
