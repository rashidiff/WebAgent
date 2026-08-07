import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import ValidationError

from backend.agent import SessionCoordinator, run_browser_agent
from backend.database import count_sessions, get_session_history, init_db, list_sessions
from backend.schemas import ActionResultEvent, SessionHistoryResponse, SessionListResponse, UserInputEvent
from backend.settings import get_settings

load_dotenv(override=True)
settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("browser_agent.main")

AUTH_TOKEN = settings.agent_auth_token.strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Browser Agent Backend",
    description="Local FastAPI WebSocket server directing the Web Browser AI Agent.",
    version="1.0.0",
    lifespan=lifespan
)


# CORS is only relevant to the plain HTTP endpoints (GET /sessions*); the extension talks to
# this server exclusively over WebSocket, which CORS does not gate. Default to allowing no
# cross-origin browser access at all, since /sessions exposes locally logged chat/action
# history and a wildcard origin would let any open webpage's JS read it via fetch().
_cors_origins = settings.cors_origins
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["X-Agent-Token"],
    )


def require_http_auth(request: Request) -> None:
    if not AUTH_TOKEN:
        return
    supplied = request.headers.get("X-Agent-Token") or request.query_params.get("token")
    if supplied != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing local agent token.")


def websocket_authorized(websocket: WebSocket) -> bool:
    if not AUTH_TOKEN:
        return True
    supplied = websocket.query_params.get("token")
    return supplied == AUTH_TOKEN

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not websocket_authorized(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Extension sidepanel connected")
    
    coordinator = SessionCoordinator(websocket)
    await coordinator.history.start_session()

    try:
        while True:
            # Receive data packets from extension client
            data = await websocket.receive_json()
            event_type = data.get("type")
            
            logger.info("WebSocket event received: %s", event_type)
            
            if event_type == "user_input":
                user_event = UserInputEvent.model_validate(data)
                prompt = user_event.prompt.strip()
                dom_tree = [item.model_dump(exclude_none=True) for item in user_event.dom_tree]

                if not prompt:
                    await coordinator.send_status("ERROR: Prompt cannot be empty.")
                    continue

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
                action_result = ActionResultEvent.model_validate(data)
                await coordinator.response_queue.put(action_result.model_dump(exclude_none=True))
                
            elif event_type == "stop_agent":
                coordinator.is_running = False
                if coordinator.agent_task and not coordinator.agent_task.done():
                    coordinator.agent_task.cancel()
                    logger.info("Agent execution cancelled by stop signal")
                await coordinator.send_status("FINISHED: Stopped by user.")
                
            elif event_type == "reset_session":
                coordinator.is_running = False
                if coordinator.agent_task and not coordinator.agent_task.done():
                    coordinator.agent_task.cancel()
                coordinator.current_dom = []
                while not coordinator.response_queue.empty():
                    coordinator.response_queue.get_nowait()
                logger.info("Session state reset complete")
                
    except WebSocketDisconnect:
        logger.info("Extension sidepanel disconnected")
    except ValidationError as exc:
        logger.warning("Rejected invalid WebSocket payload: %s", exc)
        await websocket.close(code=1003, reason="Invalid payload")
    except Exception as e:
        logger.exception("WebSocket error encountered: %s", str(e))
    finally:
        # Make sure agent task is terminated if connection terminates
        if coordinator.agent_task and not coordinator.agent_task.done():
            coordinator.agent_task.cancel()
        coordinator.is_running = False
        await coordinator.history.end_session()


@app.get("/sessions", response_model=SessionListResponse)
async def get_sessions(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Lists all recorded agent sessions, most recent first."""
    require_http_auth(request)
    sessions = await asyncio.to_thread(list_sessions, limit, offset)
    total = await asyncio.to_thread(count_sessions)
    return SessionListResponse(sessions=sessions, total=total, limit=limit, offset=offset)


@app.get("/sessions/{session_id}", response_model=SessionHistoryResponse)
async def get_session(session_id: str, request: Request):
    """Returns the persisted messages and browser actions for a session."""
    require_http_auth(request)
    history = await asyncio.to_thread(get_session_history, session_id)
    if not history["messages"] and not history["actions"]:
        raise HTTPException(status_code=404, detail="Session not found or has no recorded history.")
    return history


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting browser agent backend server at http://%s:%s", settings.host, settings.port)
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port, reload=True)
