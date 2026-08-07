from typing import Any, Literal

from pydantic import BaseModel, Field


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
    dom_tree: list[InteractiveDomElement] = Field(default_factory=list)
    page_text: PageTextPayload | None = None
    error: str | None = None


class AgentStatusEvent(BaseModel):
    type: Literal["agent_status"]
    message: str


class AgentActionEvent(BaseModel):
    type: Literal["agent_action"]
    action: str
    selector: str | None = None
    value: str | None = None
    expected_fingerprint: str = ""
    requires_approval: bool = False
    approval_reason: str = ""


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
