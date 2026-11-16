"""Google Gemini: embeddings, summaries and streamed answers."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger("promptdox.ai")


class AIUnavailableError(RuntimeError):
    """Raised when Gemini can't be reached or isn't configured."""


@lru_cache
def _client() -> genai.Client:
    if not settings.gemini_api_key:
        raise AIUnavailableError("GEMINI_API_KEY is not set.")
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=settings.ai_timeout_seconds * 1000),
    )


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, genai_errors.APIError) and exc.code in (429, 500, 502, 503, 504)


async def _with_retries(call, attempts: int = 4):  # noqa: ANN001, ANN202
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts or not _retryable(exc):
                raise
            logger.warning("Gemini call failed (%s); retrying in %.0fs", exc, delay)
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


async def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    if not texts:
        return []
    client = _client()

    async def call() -> list[list[float]]:
        response = await client.aio.models.embed_content(
            model=settings.embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=settings.embedding_dimensions),
        )
        vectors = [list(embedding.values or []) for embedding in response.embeddings or []]
        if len(vectors) != len(texts) or any(len(v) != settings.embedding_dimensions for v in vectors):
            raise AIUnavailableError("The embedding model returned an unexpected result.")
        return vectors

    return await _with_retries(call)


async def embed_query(text: str) -> list[float]:
    return (await embed_texts([text], task_type="RETRIEVAL_QUERY"))[0]


class _SummaryResult(BaseModel):
    summary: str
    suggestions: list[str]


async def summarize_document(title: str, excerpt: str) -> tuple[str, list[str]]:
    """One call that returns a 1-2 sentence summary and three suggested questions."""
    client = _client()
    prompt = (
        "You are indexing a document for a question-answering app.\n"
        f"Document title: {title}\n\n"
        "Write:\n"
        "1. `summary`: one or two plain sentences (max 200 characters) describing what the document covers.\n"
        "2. `suggestions`: exactly three short questions (each under 60 characters) a reader could ask "
        "that are clearly answerable from the document text.\n\n"
        f"Document text:\n{excerpt}"
    )

    async def call() -> _SummaryResult:
        response = await client.aio.models.generate_content(
            model=settings.chat_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
                response_schema=_SummaryResult,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        if isinstance(response.parsed, _SummaryResult):
            return response.parsed
        return _SummaryResult.model_validate(json.loads(response.text or "{}"))

    result = await _with_retries(call)
    suggestions = [s.strip() for s in result.suggestions if s.strip()][:3]
    return result.summary.strip()[:400], [s if len(s) < 60 else s[:57].rstrip() + "?" for s in suggestions]


async def stream_answer(system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
    client = _client()
    stream = await client.aio.models.generate_content_stream(
        model=settings.chat_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    async for chunk in stream:
        if chunk.text:
            yield chunk.text
