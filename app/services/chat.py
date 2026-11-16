"""Document chat: retrieval, prompt construction, citation-marker conversion and SSE streaming."""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utcnow
from app.models import Document, Message, User
from app.schemas.document import MessageOut
from app.services import ai
from app.services.retrieval import RetrievedParagraph, search_document
from app.services.users import count_query

logger = logging.getLogger("promptdox.chat")

NOT_COVERED = (
    "The document doesn't say. I couldn't find a passage that answers this question, "
    "so try rephrasing it with terms that appear in the document."
)
HISTORY_MESSAGES = 6

# A source tag from the model: [S1], [S1, S3], [s2; S4], or bare [2].
_TAG = re.compile(r"\[\s*[Ss]?\d+(?:\s*[,;]\s*[Ss]?\d+)*\s*\]")
_PARTIAL_TAG = re.compile(r"\[\s*(?:[Ss]?\d*(?:\s*[,;]\s*[Ss]?\d*)*)?\s*$")


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def message_json(message: Message) -> dict[str, Any]:
    return MessageOut.model_validate(message).model_dump(by_alias=True, mode="json")


@dataclass
class CitationStreamer:
    """Rewrites the model's [S#] source tags into the contract's [n] markers as text streams in.

    `[n]` refers to `citations[n - 1]`, numbered in order of first use. A `citation` event is
    emitted before the `delta` that contains its marker. Tags for unknown sources are dropped.
    """

    sources: list[RetrievedParagraph]
    citations: list[dict[str, int]] = field(default_factory=list)
    content: str = ""
    _buffer: str = ""
    _index_by_source: dict[int, int] = field(default_factory=dict)

    def _markers(self, tag: str) -> tuple[list[str], str]:
        events: list[str] = []
        markers = ""
        for number in (int(n) for n in re.findall(r"\d+", tag)):
            if not 1 <= number <= len(self.sources):
                continue
            if number not in self._index_by_source:
                source = self.sources[number - 1]
                self.citations.append({"page": source.page, "paragraph": source.paragraph})
                index = len(self.citations)
                self._index_by_source[number] = index
                events.append(sse("citation", {"index": index, "page": source.page, "paragraph": source.paragraph}))
            marker = f"[{self._index_by_source[number]}]"
            if marker not in markers:
                markers += marker
        return events, markers

    def _delta(self, text: str) -> list[str]:
        if not text:
            return []
        self.content += text
        return [sse("delta", {"text": text})]

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        events: list[str] = []
        pending = ""
        while match := _TAG.search(self._buffer):
            pending += self._buffer[: match.start()]
            citation_events, markers = self._markers(match.group(0))
            if markers:
                events += citation_events
                events += self._delta(pending + markers)
                pending = ""
            self._buffer = self._buffer[match.end() :]
        # Hold back a trailing "[S1, " that may complete in the next chunk.
        bracket = self._buffer.rfind("[")
        if bracket != -1 and len(self._buffer) - bracket < 40 and _PARTIAL_TAG.fullmatch(self._buffer[bracket:]):
            pending += self._buffer[:bracket]
            self._buffer = self._buffer[bracket:]
        else:
            pending += self._buffer
            self._buffer = ""
        return events + self._delta(pending)

    def finish(self) -> list[str]:
        remaining, self._buffer = self._buffer, ""
        return self._delta(remaining)


def build_prompts(
    document_name: str,
    question: str,
    sources: list[RetrievedParagraph],
    history: list[Message],
    strict: bool,
) -> tuple[str, str]:
    grounding = (
        f'If the sources do not answer the question, reply exactly: "{NOT_COVERED}" and use no tags.'
        if strict
        else "If the sources do not fully answer the question, say so, then you may add brief general "
        "knowledge clearly labelled as not coming from the document (without tags)."
    )
    system_prompt = (
        "You are PromptDox, an assistant that answers questions about a single document using only the "
        "numbered source passages provided.\n"
        "Rules:\n"
        "- Answer concisely in plain prose (2-5 sentences unless the question needs more). No headings.\n"
        "- After every sentence that uses a source, cite it with its tag, e.g. [S1] or [S1][S3]. "
        "Only cite tags that appear in the sources. Never invent page numbers.\n"
        f"- {grounding}\n"
        "- Do not mention 'sources', 'passages' or tags in words; just cite."
    )
    source_text = "\n\n".join(
        f"[S{i}] (page {s.page}, § {s.heading}, paragraph {s.paragraph})\n{s.text}"
        for i, s in enumerate(sources, start=1)
    )
    conversation = "\n".join(
        f"{'User' if m.role == 'user' else 'Assistant'}: {re.sub(r'\[\d+\]', '', m.content).strip()}"
        for m in history
    )
    user_prompt = (
        f"Document: {document_name}\n\n"
        f"Sources:\n{source_text or '(no relevant passages found)'}\n\n"
        + (f"Earlier conversation (for context only):\n{conversation}\n\n" if conversation else "")
        + f"Question: {question}"
    )
    return system_prompt, user_prompt


async def recent_history(db: AsyncSession, document_id: str, user_id: str) -> list[Message]:
    rows = await db.scalars(
        select(Message)
        .where(Message.document_id == document_id, Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_MESSAGES)
    )
    return list(reversed(list(rows)))


@dataclass(frozen=True)
class ChatContext:
    user_id: str
    workspace_id: str
    document_id: str
    document_name: str
    chunk_count: int
    strict: bool


async def stream_chat(request: Request, context: ChatContext, question: str) -> AsyncIterator[str]:
    """Yield SSE events: status(searching) -> status(writing) -> [citation|delta]* -> done | error."""
    asked_at = utcnow()
    try:
        yield sse("status", {"stage": "searching", "passages": context.chunk_count})

        query_vector = await ai.embed_query(question)
        async with SessionLocal() as db:
            retrieved = await search_document(
                db,
                workspace_id=context.workspace_id,
                document_id=context.document_id,
                query_vector=query_vector,
            )
            history = await recent_history(db, context.document_id, context.user_id)

        relevant = [r for r in retrieved if r.score >= settings.min_relevance]
        sources = relevant if context.strict else retrieved
        streamer = CitationStreamer(sources=sources)

        yield sse("status", {"stage": "writing"})

        if context.strict and not sources:
            for event in streamer.feed(NOT_COVERED):
                yield event
        else:
            system_prompt, user_prompt = build_prompts(
                context.document_name, question, sources, history, context.strict
            )
            async for text in ai.stream_answer(system_prompt, user_prompt):
                if await request.is_disconnected():
                    logger.info("Client disconnected mid-stream; discarding answer")
                    return
                for event in streamer.feed(text):
                    yield event
        for event in streamer.finish():
            yield event

        content = streamer.content.strip()
        if not content:
            raise ai.AIUnavailableError("The model returned an empty answer.")
        if NOT_COVERED in content:
            # A "not covered" reply never carries citations.
            content, streamer.citations = NOT_COVERED, []

        if await request.is_disconnected():
            return

        # Save both messages and count the question only now that the answer is complete.
        async with SessionLocal() as db:
            user = await db.get(User, context.user_id)
            document = await db.get(Document, context.document_id)
            if user is None or document is None or document.workspace_id != context.workspace_id:
                yield sse("error", {"code": "document_not_found", "message": "This document no longer exists."})
                return
            user_message = Message(
                document_id=context.document_id,
                user_id=context.user_id,
                role="user",
                content=question,
                citations=[],
                created_at=asked_at,
            )
            assistant_message = Message(
                document_id=context.document_id,
                user_id=context.user_id,
                role="assistant",
                content=content,
                citations=streamer.citations,
                created_at=utcnow(),
            )
            db.add_all([user_message, assistant_message])
            queries_today = count_query(user)
            await db.commit()
            payload = {
                "userMessage": message_json(user_message),
                "message": message_json(assistant_message),
                "usage": {"queriesToday": queries_today},
            }
        yield sse("done", payload)
    except Exception:  # noqa: BLE001  (after streaming starts, errors become an `error` event)
        logger.exception("Chat stream failed for document %s", context.document_id)
        yield sse(
            "error",
            {
                "code": "internal_error",
                "message": "Something went wrong while writing the answer. Please try again.",
            },
        )
