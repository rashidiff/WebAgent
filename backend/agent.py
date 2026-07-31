import os
import asyncio
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from database import HistoryStore

load_dotenv(override=True)

DEFAULT_ACTION_TIMEOUT_SECONDS = float(os.getenv("ACTION_TIMEOUT_SECONDS", "40"))
DEFAULT_MAX_DOM_ELEMENTS = int(os.getenv("MAX_DOM_ELEMENTS", "150"))
DEFAULT_MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "15"))
REQUIRE_ACTION_APPROVAL = os.getenv("REQUIRE_ACTION_APPROVAL", "true").lower() not in {"0", "false", "no"}
SENSITIVE_ACTION_KEYWORDS = [
    "submit", "send", "buy", "purchase", "pay", "checkout", "order", "delete", "remove",
    "confirm", "transfer", "withdraw", "sign", "agree", "accept", "place order", "book",
]
logger = logging.getLogger("browser_agent.agent")

# Factory function to obtain the configured Chat LLM
def get_llm():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model_name = os.getenv("LLM_MODEL_NAME")
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2048"))

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key or "your_" in api_key:
            # Fallback to general GOOGLE_API_KEY
            api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key or "your_" in api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured in your .env file.")
            
        from langchain_google_genai import ChatGoogleGenerativeAI
        if not model_name or "gemini" not in model_name:
            model_name = "gemini-1.5-flash"

        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=0.0,
            max_output_tokens=max_tokens
        )
        
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or "your_" in api_key:
            raise ValueError("OPENAI_API_KEY is not configured in your .env file.")
            
        from langchain_openai import ChatOpenAI
        if not model_name or "gpt" not in model_name:
            model_name = "gpt-4o-mini"
            
        return ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            temperature=0.0,
            max_tokens=max_tokens
        )

    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or "your_" in api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured in your .env file.")
            
        try:
            from langchain_anthropic import ChatAnthropic
            if not model_name or "claude" not in model_name:
                model_name = "claude-3-5-sonnet-latest"
            return ChatAnthropic(
                model=model_name,
                anthropic_api_key=api_key,
                temperature=0.0,
                max_tokens=max_tokens
            )
        except ImportError:
            raise ImportError("langchain-anthropic package is not installed. Please run: pip install langchain-anthropic")
            
    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key or "your_" in api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured in your .env file.")
            
        from langchain_openai import ChatOpenAI
        if not model_name or "deepseek" not in model_name:
            model_name = "deepseek-chat"
            
        return ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            openai_api_base="https://api.deepseek.com",
            temperature=0.0,
            max_tokens=max_tokens
        )
        
    else:
        raise ValueError(f"Unsupported LLM provider '{provider}'. Please use 'gemini', 'openai', 'anthropic', or 'deepseek'.")


# Coordinator that sits between the WebSocket endpoint and the LangChain tools execution thread
class SessionCoordinator:
    def __init__(self, websocket):
        self.websocket = websocket
        self.current_dom: List[Dict[str, Any]] = []
        self.response_queue = asyncio.Queue()
        self.is_running = True
        self.agent_task = None
        self.history = HistoryStore()
        self.turn_summaries: List[str] = []
        self.last_page_text: Dict[str, Any] = {}
        self.successful_actions: List[str] = []
        self.current_goal = ""

    def record_turn(self, user_prompt: str, outcome: str) -> None:
        """Remembers a short summary of a completed task so follow-up prompts in the
        same session have continuity, without re-feeding full DOM/tool history."""
        url = self.last_page_text.get("url") or "unknown URL"
        actions = "; ".join(self.successful_actions[-5:]) or "no successful actions recorded"
        summary = f'- Task: "{user_prompt[:150]}" at {url} -> {outcome[:200]} | recent actions: {actions}'
        self.turn_summaries.append(summary)
        self.turn_summaries = self.turn_summaries[-5:]

    async def send_status(self, message: str):
        """Sends real-time status update to the Chrome Extension sidepanel."""
        if self.websocket:
            await self.websocket.send_json({
                "type": "agent_status",
                "message": message
            })

    async def execute_action(self, action: str, selector: str = None, value: str = None) -> str:
        """Sends action to Chrome Extension, pauses execution, and waits for updated DOM tree."""
        if not self.is_running:
            return "Error: Agent run has been stopped by the user."
            
        # Clean queue before waiting
        while not self.response_queue.empty():
            self.response_queue.get_nowait()
            
        approval_reason = self.get_approval_reason(action, selector, value)
        expected_fingerprint = self.get_expected_fingerprint(selector)

        # Send action to extension via WebSocket
        await self.websocket.send_json({
            "type": "agent_action",
            "action": action,
            "selector": selector,
            "value": value,
            "expected_fingerprint": expected_fingerprint,
            "requires_approval": bool(approval_reason),
            "approval_reason": approval_reason
        })
        
        # Await response from content script with timeout
        try:
            response = await asyncio.wait_for(self.response_queue.get(), timeout=DEFAULT_ACTION_TIMEOUT_SECONDS)
            if response.get("status") == "success":
                self.current_dom = response.get("dom_tree", [])
                if response.get("page_text"):
                    self.last_page_text = response.get("page_text") or {}
                await self.history.log_action(action, selector, value, status="success")
                self.successful_actions.append(f"{action}({selector or value or 'page'})")
                self.successful_actions = self.successful_actions[-20:]
                result = f"Success: Action executed. Current webpage interactive elements:\n{self.format_dom_for_llm(self.current_dom)}"
                if action == "get_text" and self.last_page_text:
                    result += f"\n\nCurrent page text:\n{self.format_page_text_for_llm(self.last_page_text)}"
                return result
            else:
                err = response.get("error", "Unknown client error")
                await self.history.log_action(action, selector, value, status="error", detail=err)
                return f"Error: Action failed: {err}. Webpage interactive elements remain:\n{self.format_dom_for_llm(self.current_dom)}"
        except asyncio.TimeoutError:
            await self.history.log_action(action, selector, value, status="timeout")
            return f"Error: Browser timed out waiting for action response. Webpage interactive elements remain:\n{self.format_dom_for_llm(self.current_dom)}"

    def get_approval_reason(self, action: str, selector: str = None, value: str = None) -> str:
        if not REQUIRE_ACTION_APPROVAL:
            return ""

        if action in {"navigate", "back", "forward", "reload", "scroll", "hover", "wait", "get_text", "key"}:
            return ""

        element = next((el for el in self.current_dom if el.get("selector") == selector), {})
        element_text = " ".join(
            str(element.get(key) or "")
            for key in ["text", "label", "ariaLabel", "title", "name", "placeholder", "value", "href", "formAction"]
        ).lower()
        value_text = str(value or "").lower()
        combined = f"{action} {element_text} {value_text}"

        if any(keyword in combined for keyword in SENSITIVE_ACTION_KEYWORDS):
            return f"Sensitive browser action requires approval: {action} on {selector or 'page'}"

        input_type = str(element.get("type") or "").lower()
        if action == "input" and input_type in {"password", "email", "tel", "number"}:
            return f"Sensitive input field requires approval: {input_type}"

        return ""

    def get_expected_fingerprint(self, selector: str = None) -> str:
        if not selector:
            return ""
        element = next((el for el in self.current_dom if el.get("selector") == selector), {})
        return element.get("fingerprint") or ""

    MAX_DOM_ELEMENTS = DEFAULT_MAX_DOM_ELEMENTS

    def format_page_text_for_llm(self, page_text: Dict[str, Any]) -> str:
        if not page_text:
            return "[No page text captured]"
        title = page_text.get("title") or ""
        url = page_text.get("url") or ""
        text = page_text.get("text") or ""
        return f"Title: {title}\nURL: {url}\nText: {text[:6000]}"

    def format_dom_for_llm(self, dom: List[Dict[str, Any]]) -> str:
        """Formats the list of interactive DOM elements as a clean, structured text representation."""
        if not dom:
            return "[Empty page or no interactive elements found]"

        ranked_dom = self.rank_dom_for_goal(dom)
        truncated = len(ranked_dom) > self.MAX_DOM_ELEMENTS
        dom = ranked_dom[:self.MAX_DOM_ELEMENTS]

        lines = []
        for el in dom:
            parts = [f"ID: {el.get('id')}", f"<{el.get('tagName')}>"]
            if el.get('text'):
                parts.append(f"text: \"{el.get('text')}\"")
            if el.get('placeholder'):
                parts.append(f"placeholder: \"{el.get('placeholder')}\"")
            if el.get('label'):
                parts.append(f"label: \"{el.get('label')}\"")
            if el.get('ariaLabel'):
                parts.append(f"aria-label: \"{el.get('ariaLabel')}\"")
            if el.get('title'):
                parts.append(f"title: \"{el.get('title')}\"")
            if el.get('name'):
                parts.append(f"name: \"{el.get('name')}\"")
            if el.get('role'):
                parts.append(f"role: \"{el.get('role')}\"")
            if el.get('value'):
                parts.append(f"value: \"{el.get('value')}\"")
            if el.get('type'):
                parts.append(f"type: \"{el.get('type')}\"")
            if el.get('href'):
                parts.append(f"href: \"{el.get('href')}\"")
            if el.get('disabled'):
                parts.append("disabled: true")
            if el.get('checked'):
                parts.append("checked: true")
            if el.get('fingerprint'):
                parts.append(f"fingerprint: {el.get('fingerprint')}")
            if el.get('inViewport') is not None:
                parts.append(f"inViewport: {el.get('inViewport')}")
            if el.get('options'):
                option_text = ", ".join(
                    f"{opt.get('text') or opt.get('value')}={opt.get('value')}"
                    for opt in el.get('options', [])[:10]
                )
                parts.append(f"options: [{option_text}]")
                
            parts.append(f"selector: {el.get('selector')}")
            lines.append(" | ".join(parts))

        if truncated:
            lines.append(f"[... truncated to first {self.MAX_DOM_ELEMENTS} elements; scroll to reveal more ...]")

        return "\n".join(lines)

    def rank_dom_for_goal(self, dom: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        goal_terms = {
            term.lower()
            for term in self.current_goal.replace('"', " ").replace("'", " ").split()
            if len(term) > 2
        }
        if not goal_terms:
            return dom

        def score(el: Dict[str, Any]) -> int:
            haystack = " ".join(
                str(el.get(key) or "")
                for key in ["text", "label", "ariaLabel", "title", "name", "placeholder", "href", "role"]
            ).lower()
            return sum(1 for term in goal_terms if term in haystack)

        return [el for _, el in sorted(enumerate(dom), key=lambda item: (-score(item[1]), item[0]))]


# Helper to instantiate tools bound to a specific session coordinator
def create_agent_tools(coordinator: SessionCoordinator):
    @tool
    async def click_element(selector: str) -> str:
        """Clicks an element on the webpage using its CSS selector (e.g. '[data-agent-id="12"]'). Use for buttons, links, inputs, options, checkboxes."""
        return await coordinator.execute_action("click", selector=selector)

    @tool
    async def input_text(selector: str, text: str) -> str:
        """Types text into an input or textarea element on the webpage using its CSS selector (e.g. '[data-agent-id="5"]')."""
        return await coordinator.execute_action("input", selector=selector, value=text)

    @tool
    async def scroll_page(direction: str) -> str:
        """Scrolls the page layout. 'direction' must be one of: 'down', 'up', 'top', 'bottom'."""
        if direction not in ["down", "up", "top", "bottom"]:
            return "Error: Invalid scroll direction. Choose from 'down', 'up', 'top', 'bottom'."
        return await coordinator.execute_action("scroll", value=direction)

    @tool
    async def navigate_url(url: str) -> str:
        """Navigates the active tab to an absolute http(s) URL."""
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://."
        return await coordinator.execute_action("navigate", value=url)

    @tool
    async def press_key(key: str) -> str:
        """Presses a keyboard key in the active page, such as Enter, Escape, Tab, ArrowDown, or Backspace."""
        return await coordinator.execute_action("key", value=key)

    @tool
    async def select_option(selector: str, value: str) -> str:
        """Selects an option in a <select> element by option value using its CSS selector."""
        return await coordinator.execute_action("select", selector=selector, value=value)

    @tool
    async def hover_element(selector: str) -> str:
        """Moves the mouse hover state over an element using its CSS selector."""
        return await coordinator.execute_action("hover", selector=selector)

    @tool
    async def go_back() -> str:
        """Navigates the active tab one step back in browser history."""
        return await coordinator.execute_action("back")

    @tool
    async def go_forward() -> str:
        """Navigates the active tab one step forward in browser history."""
        return await coordinator.execute_action("forward")

    @tool
    async def reload_page() -> str:
        """Reloads the active page."""
        return await coordinator.execute_action("reload")

    @tool
    async def get_page_text() -> str:
        """Reads the visible page text when the DOM element list is not enough to answer or verify the task."""
        return await coordinator.execute_action("get_text")

    @tool
    async def wait_for_page_change() -> str:
        """Waits briefly for page transitions, API results, animations, or DOM updates to settle."""
        return await coordinator.execute_action("wait")

    return [
        click_element,
        input_text,
        scroll_page,
        navigate_url,
        press_key,
        select_option,
        hover_element,
        go_back,
        go_forward,
        reload_page,
        get_page_text,
        wait_for_page_change,
    ]


def compact_old_tool_messages(messages: List[Any]) -> None:
    """Keeps the full DOM dump only on the most recent ToolMessage; older ones are
    collapsed to their first line so message history doesn't grow unbounded across steps."""
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    for i in tool_indices[:-1]:
        content = messages[i].content
        first_line = content.split("\n", 1)[0]
        if len(content) > len(first_line):
            messages[i] = ToolMessage(
                content=f"{first_line} [older DOM snapshot omitted for brevity]",
                tool_call_id=messages[i].tool_call_id
            )


def tool_signature(tool_name: str, tool_args: Dict[str, Any]) -> str:
    normalized = ",".join(f"{key}={tool_args.get(key)}" for key in sorted(tool_args))
    return f"{tool_name}:{normalized}"


SYSTEM_PROMPT = """You are a highly capable Browser AI Agent. Your goal is to help the user complete their tasks on the active browser tab.
You will be provided with the user's prompt and a serialized structure of the webpage's interactive elements (DOM state).

Each element is described in this format:
ID: <id> | <TAGNAME> | text: "<text>" | placeholder: "<placeholder>" | value: "<value>" | selector: [data-agent-id="<id>"]

Your task is to analyze this list, decide on the best next action, and execute it using one of these tools:
1. `click_element(selector)`: Clicks an element. Always use the selector string (e.g. `[data-agent-id="12"]`).
2. `input_text(selector, text)`: Inputs text into a target text field, textarea, or contenteditable element.
3. `scroll_page(direction)`: Scrolls the browser viewport. Use this to discover more elements if needed.
4. `navigate_url(url)`: Navigates to an absolute http(s) URL.
5. `press_key(key)`: Presses a key such as Enter, Escape, Tab, ArrowDown, or Backspace.
6. `select_option(selector, value)`: Chooses an option in a select element.
7. `hover_element(selector)`: Hovers an element to reveal menus or tooltips.
8. `go_back()`, `go_forward()`, `reload_page()`: Browser navigation controls.
9. `get_page_text()`: Reads visible page text for comprehension or verification.
10. `wait_for_page_change()`: Waits briefly for dynamic page updates.

INSTRUCTIONS:
- Think briefly before every tool call: state what you expect the action to change, then verify after the tool result.
- You must carefully verify whether your action succeeded in each step by analyzing the updated DOM state returned after the tool execution.
- If an action fails (e.g., selector not found or disabled), try scrolling, finding a parent/sibling element, or adapting your strategy.
- Keep execution steps focused. Do not repeat the same failing action or the same selector/value pair after an error.
- Use `get_page_text()` when the user asks a reading/research question or when visible non-interactive content is needed.
- Use `wait_for_page_change()` after actions that may trigger dynamic loading before deciding the result failed.
- Once you successfully achieve the user's goal (e.g., search results are displayed, items are added to cart, details are submitted), stop calling tools and summarize the completion. Your final response MUST start with:
  "SUCCESS: [description of what was accomplished and final page state]"
- If you run into blocker constraints (e.g. CAPTCHA, payment gates, missing login details, server errors), stop calling tools and respond with:
  "ERROR: [detailed reason why the task failed]"
{previous_tasks}
Current User Goal: {user_prompt}
"""

async def run_browser_agent(coordinator: SessionCoordinator, user_prompt: str, initial_dom: List[Dict[str, Any]]):
    try:
        coordinator.current_goal = user_prompt
        coordinator.current_dom = initial_dom
        await coordinator.history.log_message("user", user_prompt)
        llm = get_llm()
        tools = create_agent_tools(coordinator)
        
        # Map tool names to tool objects
        tool_map = {t.name: t for t in tools}
        
        # Bind the tools to the LLM
        llm_with_tools = llm.bind_tools(tools)

        previous_tasks = ""
        if coordinator.turn_summaries:
            previous_tasks = (
                "\nPREVIOUS TASKS COMPLETED IN THIS SESSION (for context only, do not repeat them):\n"
                + "\n".join(coordinator.turn_summaries) + "\n"
            )

        system_instructions = SYSTEM_PROMPT.format(user_prompt=user_prompt, previous_tasks=previous_tasks)
        
        # Seed message history
        messages = [
            SystemMessage(content=system_instructions),
            HumanMessage(content=f"Current webpage interactive elements:\n{coordinator.format_dom_for_llm(initial_dom)}\n\nTask: {user_prompt}")
        ]
        
        max_steps = DEFAULT_MAX_AGENT_STEPS
        step = 0
        failed_tool_signatures = set()
        
        await coordinator.send_status("Agent thinking and planning first action...")
        
        while step < max_steps and coordinator.is_running:
            step += 1
            logger.info("Agent loop step %s", step)

            # Keep only the latest DOM snapshot in full; older ones are collapsed
            compact_old_tool_messages(messages)

            # Invoke LLM (fully async)
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)
            
            # Check if model requested any tool calls
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    tool_id = tool_call["id"]
                    signature = tool_signature(tool_name, tool_args)
                    
                    logger.info("Executing tool %s with args %s", tool_name, tool_args)
                    await coordinator.send_status(
                        f"PLAN: Step {step}: {tool_name} with {tool_args}. Verifying after execution."
                    )
                    
                    if signature in failed_tool_signatures:
                        tool_result = (
                            "Error: This exact action already failed earlier in this run. "
                            "Choose a different selector, scroll, read page text, or adapt the strategy."
                        )
                    elif tool_name in tool_map:
                        try:
                            # Invoke tool, which communicates over WebSockets
                            tool_result = await tool_map[tool_name].ainvoke(tool_args)
                        except Exception as tool_err:
                            tool_result = f"Error executing tool: {str(tool_err)}"
                    else:
                        tool_result = f"Error: Tool {tool_name} not found."
                    
                    # Append execution feedback as ToolMessage
                    messages.append(ToolMessage(
                        content=tool_result,
                        tool_call_id=tool_id
                    ))

                    if tool_result.startswith("Error:"):
                        failed_tool_signatures.add(signature)
                
                await coordinator.send_status("VERIFY: Action completed. Analyzing updated page state...")
            else:
                # No tool calls means agent outputted its final response
                output = response.content.strip()
                logger.info("Agent final answer: %s", output)
                
                if not (output.startswith("SUCCESS:") or output.startswith("ERROR:") or output.startswith("FINISHED:")):
                    output = f"FINISHED: {output}"

                await coordinator.history.log_message("assistant", output)
                coordinator.record_turn(user_prompt, output)
                await coordinator.send_status(output)
                return

        if step >= max_steps:
            limit_msg = "ERROR: Reached maximum execution limit of 15 steps without completion."
            await coordinator.history.log_message("assistant", limit_msg)
            coordinator.record_turn(user_prompt, limit_msg)
            await coordinator.send_status(limit_msg)

    except asyncio.CancelledError:
        logger.info("Agent execution was cancelled")
        cancel_msg = "ERROR: Agent execution stopped by user command."
        await coordinator.history.log_message("assistant", cancel_msg)
        coordinator.record_turn(user_prompt, cancel_msg)
        await coordinator.send_status(cancel_msg)
    except Exception as e:
        error_msg = f"ERROR: Execution failed: {str(e)}"
        logger.exception(error_msg)
        await coordinator.history.log_message("assistant", error_msg)
        coordinator.record_turn(user_prompt, error_msg)
        await coordinator.send_status(error_msg)
    finally:
        coordinator.is_running = False
