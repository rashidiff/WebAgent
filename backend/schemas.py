from typing import Any, Literal

from pydantic import BaseModel, Field


BrowserAction = Literal[
    "click",
    "input",
    "scroll",
    "navigate",
    "key",
    "select",
    "hover",
    "back",
    "forward",
    "reload",
    "get_text",
    "wait",
]


class InteractiveDomElement(BaseModel):
    id: int | None = None
    tagName: str | None = None
    type: str | None = None
    text: str | None = None
    label: str | None = None
    ariaLabel: str | None = None
    title: str | None = None
    name: str | None = None
    elementId: str | None = None
    className: str | None = None
    role: str | None = None
    placeholder: str | None = None
    value: str | None = None
    selector: str | None = None
    fingerprint: str | None = None
    disabled: bool | None = None
    checked: bool | None = None
    href: str | None = None
    formAction: str | None = None
    options: list[dict[str, Any]] | None = None
    rect: dict[str, Any] | None = None
    inViewport: bool | None = None


class PageTextPayload(BaseModel):
    title: str | None = None
    url: str | None = None
    text: str | None = None


class UserInputEvent(BaseModel):
    type: Literal["user_input"]
    prompt: str
    dom_tree: list[InteractiveDomElement] = Field(default_factory=list)


class ActionResultEvent(BaseModel):
    type: Literal["action_result"]
    status: Literal["success", "error"]
    run_id: str | None = None
    action_id: str | None = None
    dom_tree: list[InteractiveDomElement] = Field(default_factory=list)
    page_text: PageTextPayload | None = None
    error: str | None = None


class AgentStatusEvent(BaseModel):
    type: Literal["agent_status"]
    message: str


class AgentActionEvent(BaseModel):
    type: Literal["agent_action"]
    action: BrowserAction
    run_id: str
    action_id: str
    selector: str | None = None
    value: str | None = None
    expected_fingerprint: str = ""
    requires_approval: bool = False
    approval_reason: str = ""
    risk_level: str = "low"
    target_summary: str = ""


class SessionSummary(BaseModel):
    id: str
    started_at: str
    ended_at: str | None = None


class MessageRecord(BaseModel):
    role: str
    content: str
    created_at: str


class ActionRecord(BaseModel):
    action: str
    selector: str | None = None
    value: str | None = None
    status: str
    detail: str | None = None
    created_at: str


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageRecord]
    actions: list[ActionRecord]


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int
    limit: int
    offset: int


class RunStepRecord(BaseModel):
    id: int
    run_id: str
    step_index: int
    event_type: str
    title: str
    detail: str | None = None
    action: str | None = None
    selector: str | None = None
    value: str | None = None
    url: str | None = None
    page_title: str | None = None
    dom_summary: str | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunSummary(BaseModel):
    id: str
    session_id: str | None = None
    prompt: str
    status: str
    plan: str | None = None
    started_at: str
    ended_at: str | None = None


class RunDetail(RunSummary):
    steps: list[RunStepRecord] = Field(default_factory=list)


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int
    limit: int
    offset: int


class WorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt_template: str = Field(min_length=1)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    source_run_id: str | None = None


class WorkflowRecord(BaseModel):
    id: str
    name: str
    prompt_template: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    source_run_id: str | None = None
    created_at: str
    updated_at: str


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowRecord]
