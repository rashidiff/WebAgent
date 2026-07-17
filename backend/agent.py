import os
import asyncio
from typing import Dict, Any, List
from dotenv import load_dotenv

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from database import HistoryStore

load_dotenv(override=True)

# Factory function to obtain the configured Chat LLM
def get_llm():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model_name = os.getenv("LLM_MODEL_NAME")
    
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
            temperature=0.0
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
            temperature=0.0
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
                temperature=0.0
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
            temperature=0.0
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
            
        # Send action to extension via WebSocket
        await self.websocket.send_json({
            "type": "agent_action",
            "action": action,
            "selector": selector,
            "value": value
        })
        
        # Await response from content script with timeout
        try:
            response = await asyncio.wait_for(self.response_queue.get(), timeout=40.0)
            if response.get("status") == "success":
                self.current_dom = response.get("dom_tree", [])
                await self.history.log_action(action, selector, value, status="success")
                return f"Success: Action executed. Current webpage interactive elements:\n{self.format_dom_for_llm(self.current_dom)}"
            else:
                err = response.get("error", "Unknown client error")
                await self.history.log_action(action, selector, value, status="error", detail=err)
                return f"Error: Action failed: {err}. Webpage interactive elements remain:\n{self.format_dom_for_llm(self.current_dom)}"
        except asyncio.TimeoutError:
            await self.history.log_action(action, selector, value, status="timeout")
            return f"Error: Browser timed out waiting for action response. Webpage interactive elements remain:\n{self.format_dom_for_llm(self.current_dom)}"

    MAX_DOM_ELEMENTS = 150

    def format_dom_for_llm(self, dom: List[Dict[str, Any]]) -> str:
        """Formats the list of interactive DOM elements as a clean, structured text representation."""
        if not dom:
            return "[Empty page or no interactive elements found]"

        truncated = len(dom) > self.MAX_DOM_ELEMENTS
        dom = dom[:self.MAX_DOM_ELEMENTS]

        lines = []
        for el in dom:
            parts = [f"ID: {el.get('id')}", f"<{el.get('tagName')}>"]
            if el.get('text'):
                parts.append(f"text: \"{el.get('text')}\"")
            if el.get('placeholder'):
                parts.append(f"placeholder: \"{el.get('placeholder')}\"")
            if el.get('value'):
                parts.append(f"value: \"{el.get('value')}\"")
            if el.get('type'):
                parts.append(f"type: \"{el.get('type')}\"")
            if el.get('href'):
                parts.append(f"href: \"{el.get('href')}\"")
                
            parts.append(f"selector: {el.get('selector')}")
            lines.append(" | ".join(parts))

        if truncated:
            lines.append(f"[... truncated to first {self.MAX_DOM_ELEMENTS} elements; scroll to reveal more ...]")

        return "\n".join(lines)


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

    return [click_element, input_text, scroll_page]


SYSTEM_PROMPT = """You are a highly capable Browser AI Agent. Your goal is to help the user complete their tasks on the active browser tab.
You will be provided with the user's prompt and a serialized structure of the webpage's interactive elements (DOM state).

Each element is described in this format:
ID: <id> | <TAGNAME> | text: "<text>" | placeholder: "<placeholder>" | value: "<value>" | selector: [data-agent-id="<id>"]

Your task is to analyze this list, decide on the best next action, and execute it using one of these tools:
1. `click_element(selector)`: Clicks an element. Always use the selector string (e.g. `[data-agent-id="12"]`).
2. `input_text(selector, text)`: Inputs text into a target text field or input.
3. `scroll_page(direction)`: Scrolls the browser viewport. Use this to discover more elements if needed.

INSTRUCTIONS:
- You must carefully verify whether your action succeeded in each step by analyzing the updated DOM state returned after the tool execution.
- If an action fails (e.g., selector not found or disabled), try scrolling, finding a parent/sibling element, or adapting your strategy.
- Keep execution steps focused. Do not repeat the same failing action.
- Once you successfully achieve the user's goal (e.g., search results are displayed, items are added to cart, details are submitted), stop calling tools and summarize the completion. Your final response MUST start with:
  "SUCCESS: [description of what was accomplished and final page state]"
- If you run into blocker constraints (e.g. CAPTCHA, payment gates, missing login details, server errors), stop calling tools and respond with:
  "ERROR: [detailed reason why the task failed]"

Current User Goal: {user_prompt}
"""

async def run_browser_agent(coordinator: SessionCoordinator, user_prompt: str, initial_dom: List[Dict[str, Any]]):
    try:
        coordinator.current_dom = initial_dom
        await coordinator.history.log_message("user", user_prompt)
        llm = get_llm()
        tools = create_agent_tools(coordinator)
        
        # Map tool names to tool objects
        tool_map = {t.name: t for t in tools}
        
        # Bind the tools to the LLM
        llm_with_tools = llm.bind_tools(tools)
        
        system_instructions = SYSTEM_PROMPT.format(user_prompt=user_prompt)
        
        # Seed message history
        messages = [
            SystemMessage(content=system_instructions),
            HumanMessage(content=f"Current webpage interactive elements:\n{coordinator.format_dom_for_llm(initial_dom)}\n\nTask: {user_prompt}")
        ]
        
        max_steps = 15
        step = 0
        
        await coordinator.send_status("Agent thinking and planning first action...")
        
        while step < max_steps and coordinator.is_running:
            step += 1
            print(f"[Agent Loop] Step {step}...")
            
            # Invoke LLM (fully async)
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)
            
            # Check if model requested any tool calls
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    tool_id = tool_call["id"]
                    
                    print(f"[Agent Loop] Executing tool {tool_name} with args {tool_args}...")
                    
                    if tool_name in tool_map:
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
                
                await coordinator.send_status("Action completed. Analyzing updated page state...")
            else:
                # No tool calls means agent outputted its final response
                output = response.content.strip()
                print(f"[Agent Loop] Final Answer: {output}")
                
                if not (output.startswith("SUCCESS:") or output.startswith("ERROR:") or output.startswith("FINISHED:")):
                    output = f"FINISHED: {output}"

                await coordinator.history.log_message("assistant", output)
                await coordinator.send_status(output)
                return

        if step >= max_steps:
            limit_msg = "ERROR: Reached maximum execution limit of 15 steps without completion."
            await coordinator.history.log_message("assistant", limit_msg)
            await coordinator.send_status(limit_msg)

    except asyncio.CancelledError:
        print("Agent execution was cancelled.")
        await coordinator.history.log_message("assistant", "ERROR: Agent execution stopped by user command.")
        await coordinator.send_status("ERROR: Agent execution stopped by user command.")
    except Exception as e:
        error_msg = f"ERROR: Execution failed: {str(e)}"
        print(error_msg)
        await coordinator.history.log_message("assistant", error_msg)
        await coordinator.send_status(error_msg)
    finally:
        coordinator.is_running = False
