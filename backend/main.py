import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import ValidationError

from backend.agent import SessionCoordinator, run_browser_agent
from backend.database import (
    clear_sessions,
    count_runs,
    count_sessions,
    create_workflow,
    delete_run,
    delete_session,
    delete_workflow,
    get_run,
    get_session_history,
    init_db,
    list_runs,
    list_sessions,
    list_workflows,
)
from backend.schemas import (
    ActionResultEvent,
    RunDetail,
    RunListResponse,
    SessionHistoryResponse,
    SessionListResponse,
    UserInputEvent,
    WorkflowCreateRequest,
    WorkflowListResponse,
    WorkflowRecord,
)
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


def render_run_markdown(run: dict) -> str:
    lines = [
        f"# WebAgent Replay: {run['id']}",
        "",
        f"- Status: {run['status']}",
        f"- Prompt: {run['prompt']}",
        f"- Started: {run['started_at']}",
    ]
    if run.get("plan"):
        lines.extend(["", "## Plan", "", run["plan"]])
    lines.extend(["", "## Steps"])
    for step in run.get("steps", []):
        lines.append(f"- Step {step['step_index']} [{step['event_type']}]: {step['title']}")
        if step.get("detail"):
            lines.append(f"  {step['detail']}")
        if step.get("url"):
            lines.append(f"  URL: {step['url']}")
        if step.get("screenshot_after"):
            lines.append("  Screenshot: available in HTML export")
    lines.append("")
    return "\n".join(lines)


def render_run_html(run: dict) -> str:
    def esc(value: object) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    step_html = []
    for step in run.get("steps", []):
        before = f"<figure><figcaption>Before</figcaption><img src='{esc(step.get('screenshot_before'))}'></figure>" if step.get("screenshot_before") else ""
        after = f"<figure><figcaption>After</figcaption><img src='{esc(step.get('screenshot_after'))}'></figure>" if step.get("screenshot_after") else ""
        step_html.append(
            "<article class='step'>"
            f"<h2>Step {step['step_index']} <span>{esc(step['event_type'])}</span></h2>"
            f"<p><strong>{esc(step['title'])}</strong></p>"
            f"<p>{esc(step.get('detail'))}</p>"
            f"<p class='url'>{esc(step.get('url'))}</p>"
            f"<div class='shots'>{before}{after}</div>"
            "</article>"
        )
    steps = "\n".join(step_html)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>WebAgent Replay {esc(run['id'])}</title>
  <style>
    body {{ font-family: Inter, system-ui, sans-serif; margin: 32px; background: #101113; color: #eceff4; }}
    .step {{ border: 1px solid #30343b; border-radius: 8px; margin: 16px 0; padding: 16px; background: #181a1f; }}
    h1, h2 {{ margin: 0 0 8px; }}
    h2 span {{ color: #9fbff7; font-size: 0.75em; text-transform: uppercase; }}
    .url {{ color: #9aa3af; word-break: break-all; }}
    .shots {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-top: 12px; }}
    figure {{ margin: 0; }}
    figcaption {{ color: #9fbff7; font-size: 12px; margin-bottom: 6px; text-transform: uppercase; }}
    img {{ width: 100%; border: 1px solid #30343b; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>WebAgent Replay</h1>
  <p><strong>Status:</strong> {esc(run['status'])}</p>
  <p><strong>Prompt:</strong> {esc(run['prompt'])}</p>
  <p><strong>Started:</strong> {esc(run['started_at'])}</p>
  <h2>Plan</h2>
  <p>{esc(run.get('plan'))}</p>
  {steps}
</body>
</html>"""

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
                if (
                    action_result.run_id
                    and coordinator.current_run_id
                    and action_result.run_id != coordinator.current_run_id
                ):
                    logger.warning("Ignoring stale action result for run_id=%s", action_result.run_id)
                    continue
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


@app.delete("/sessions")
async def delete_all_sessions(request: Request):
    """Deletes all locally recorded session history."""
    require_http_auth(request)
    deleted = await asyncio.to_thread(clear_sessions)
    return {"deleted": deleted}


@app.delete("/sessions/{session_id}")
async def delete_recorded_session(session_id: str, request: Request):
    """Deletes one locally recorded session and its messages/actions."""
    require_http_auth(request)
    deleted = await asyncio.to_thread(delete_session, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"deleted": 1}


@app.get("/runs", response_model=RunListResponse)
async def get_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    require_http_auth(request)
    runs = await asyncio.to_thread(list_runs, limit, offset)
    total = await asyncio.to_thread(count_runs)
    return RunListResponse(runs=runs, total=total, limit=limit, offset=offset)


@app.get("/runs/{run_id}", response_model=RunDetail)
async def get_recorded_run(run_id: str, request: Request):
    require_http_auth(request)
    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@app.delete("/runs/{run_id}")
async def delete_recorded_run(run_id: str, request: Request):
    require_http_auth(request)
    deleted = await asyncio.to_thread(delete_run, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {"deleted": 1}


@app.get("/runs/{run_id}/export")
async def export_recorded_run(run_id: str, request: Request, format: str = Query(default="markdown")):
    require_http_auth(request)
    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if format == "html":
        return Response(render_run_html(run), media_type="text/html")
    if format in {"md", "markdown"}:
        return Response(render_run_markdown(run), media_type="text/markdown")
    raise HTTPException(status_code=400, detail="format must be html or markdown.")


@app.get("/workflows", response_model=WorkflowListResponse)
async def get_workflows(request: Request):
    require_http_auth(request)
    workflows = await asyncio.to_thread(list_workflows)
    return WorkflowListResponse(workflows=workflows)


@app.post("/workflows", response_model=WorkflowRecord)
async def create_recorded_workflow(payload: WorkflowCreateRequest, request: Request):
    require_http_auth(request)
    workflow = await asyncio.to_thread(
        create_workflow,
        payload.name.strip(),
        payload.prompt_template.strip(),
        payload.steps,
        payload.source_run_id,
    )
    return workflow


@app.delete("/workflows/{workflow_id}")
async def delete_recorded_workflow(workflow_id: str, request: Request):
    require_http_auth(request)
    deleted = await asyncio.to_thread(delete_workflow, workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return {"deleted": 1}


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting browser agent backend server at http://%s:%s", settings.host, settings.port)
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port, reload=True)
