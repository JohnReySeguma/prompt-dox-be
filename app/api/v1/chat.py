from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, DbSession, get_workspace_document
from app.core.errors import AppError, document_not_ready
from app.core.ratelimit import limiter
from app.models import Message
from app.schemas.document import ChatRequest, FeedbackRequest, MessageList, MessageOut
from app.services.chat import ChatContext, stream_chat
from app.services.users import query_limit, queries_used_today

router = APIRouter(tags=["chat"])

_SSE_EXAMPLE = (
    'event: status\ndata: {"stage":"searching","passages":214}\n\n'
    'event: status\ndata: {"stage":"writing"}\n\n'
    'event: citation\ndata: {"index":1,"page":4,"paragraph":1}\n\n'
    'event: delta\ndata: {"text":"For Q4 2026, management guides revenue of $52.0M–$53.5M [1]."}\n\n'
    'event: done\ndata: {"userMessage":{...},"message":{...},"usage":{"queriesToday":65}}\n\n'
)


@router.get("/documents/{document_id}/messages", response_model=MessageList)
async def list_messages(document_id: str, user: CurrentUser, db: DbSession) -> MessageList:
    document = await get_workspace_document(db, user, document_id)
    messages = await db.scalars(
        select(Message)
        .where(Message.document_id == document.id, Message.user_id == user.id)
        .order_by(Message.created_at, Message.role.desc())
    )
    return MessageList(messages=[MessageOut.model_validate(m) for m in messages])


@router.delete("/documents/{document_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(document_id: str, user: CurrentUser, db: DbSession) -> Response:
    document = await get_workspace_document(db, user, document_id)
    await db.execute(delete(Message).where(Message.document_id == document.id, Message.user_id == user.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/documents/{document_id}/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"example": _SSE_EXAMPLE}}}},
)
async def chat(document_id: str, body: ChatRequest, request: Request, user: CurrentUser, db: DbSession):
    # All validation happens before streaming starts and returns a normal JSON error.
    limiter.hit(f"chat:{user.id}", 10, 60)
    document = await get_workspace_document(db, user, document_id)
    if document.status != "ready":
        raise document_not_ready()
    limit = query_limit(user)
    if limit is not None and queries_used_today(user) >= limit:
        raise AppError(
            429,
            "daily_query_limit_reached",
            f"You've used all {limit} questions for today. Your limit resets at midnight UTC.",
            {"limit": limit, "plan": user.plan},
        )
    context = ChatContext(
        user_id=user.id,
        workspace_id=user.workspace_id,
        document_id=document.id,
        document_name=document.name,
        chunk_count=document.chunk_count,
        strict=user.strict_citations,
    )
    return StreamingResponse(
        stream_chat(request, context, body.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def message_feedback(message_id: str, body: FeedbackRequest, user: CurrentUser, db: DbSession) -> Response:
    message = await db.scalar(
        select(Message).where(Message.id == message_id, Message.user_id == user.id, Message.role == "assistant")
    )
    if message is None:
        raise AppError(404, "message_not_found", "We couldn't find that answer.")
    message.helpful = body.helpful
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
