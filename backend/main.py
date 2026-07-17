import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from agent import SessionCoordinator, run_browser_agent
from database import init_db, list_sessions, get_session_history

load_dotenv(override=True)

app = FastAPI(
    title="Browser Agent Backend",
    description="Local FastAPI WebSocket server directing the Web Browser AI Agent.",
    version="1.0.0"
)


@app.on_event("startup")
async def on_startup():
    init_db()

# Configure CORS for local development and extension communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[WebSocket] Extension sidepanel connected.")
    
    coordinator = SessionCoordinator(websocket)
    await coordinator.history.start_session()

    try:
        while True:
            # Receive data packets from extension client
            data = await websocket.receive_json()
            event_type = data.get("type")
            
            print(f"[WebSocket] Event received: '{event_type}'")
            
            if event_type == "user_input":
                prompt = data.get("prompt")
                dom_tree = data.get("dom_tree", [])
                
                # Cancel any previous task still executing
                if coordinator.agent_task and not coordinator.agent_task.done():
                    coordinator.agent_task.cancel()
                    await asyncio.sleep(0.1) # allow task cancellation cleanup
                
                coordinator.is_running = True
                # Run the LangChain agent loop as a background task to keep WebSocket read responsive
                coordinator.agent_task = asyncio.create_task(
                    run_browser_agent(coordinator, prompt, dom_tree)
                )
                
            elif event_type == "action_result":
                # Push webpage action execution results into the coordinator queue to resume tools
                await coordinator.response_queue.put(data)
                
            elif event_type == "stop_agent":
                coordinator.is_running = False
                if coordinator.agent_task and not coordinator.agent_task.done():
                    coordinator.agent_task.cancel()
                    print("[Agent] Execution cancelled by stop signal.")
                await coordinator.send_status("FINISHED: Stopped by user.")
                
            elif event_type == "reset_session":
                coordinator.is_running = False
                if coordinator.agent_task and not coordinator.agent_task.done():
                    coordinator.agent_task.cancel()
                coordinator.current_dom = []
                while not coordinator.response_queue.empty():
                    coordinator.response_queue.get_nowait()
                print("[Session] State reset complete.")
                
    except WebSocketDisconnect:
        print("[WebSocket] Extension sidepanel disconnected.")
    except Exception as e:
        print(f"[WebSocket] Error encountered: {str(e)}")
    finally:
        # Make sure agent task is terminated if connection terminates
        if coordinator.agent_task and not coordinator.agent_task.done():
            coordinator.agent_task.cancel()
        coordinator.is_running = False
        await coordinator.history.end_session()


@app.get("/sessions")
async def get_sessions():
    """Lists all recorded agent sessions, most recent first."""
    return list_sessions()


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Returns the persisted messages and browser actions for a session."""
    history = get_session_history(session_id)
    if not history["messages"] and not history["actions"]:
        raise HTTPException(status_code=404, detail="Session not found or has no recorded history.")
    return history


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting browser agent backend server at http://{host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=True)
