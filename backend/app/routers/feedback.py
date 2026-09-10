"""Feedback endpoints: an analyst chats with a terse intake agent about a section's
accuracy. One user message in, one terse reply out; the whole conversation is persisted to
the ledger and traced (see app.feedback_service)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import feedback_service
from app.deps import get_ledger

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class ChatMessage(BaseModel):
    role: str  # analyst | agent
    text: str


class FeedbackChatRequest(BaseModel):
    company: str
    section_id: str
    section_label: str
    section_text: str
    highlighted_quote: Optional[str] = None
    session_id: Optional[str] = None
    messages: List[ChatMessage]


class FeedbackSummary(BaseModel):
    issue: str
    severity: str
    section: str


class FeedbackChatResponse(BaseModel):
    session_id: str
    reply: str
    captured: bool
    summary: Optional[FeedbackSummary] = None


@router.post("/chat", response_model=FeedbackChatResponse)
def chat(req: FeedbackChatRequest, ledger=Depends(get_ledger)) -> FeedbackChatResponse:
    # The latest analyst turn drives this call; earlier turns are reloaded from the ledger.
    latest = next(
        (m.text for m in reversed(req.messages) if m.role == "analyst" and m.text.strip()),
        "",
    )
    result = feedback_service.chat(
        store=ledger,
        company=req.company,
        section_id=req.section_id,
        section_label=req.section_label,
        section_text=req.section_text,
        user_text=latest,
        highlighted_quote=req.highlighted_quote,
        session_id=req.session_id,
    )
    return FeedbackChatResponse(**result)
