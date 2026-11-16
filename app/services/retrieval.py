"""pgvector cosine-similarity search, scoped to one document inside one workspace."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import DocumentPage, Passage


@dataclass(frozen=True)
class RetrievedParagraph:
    page: int
    paragraph: int
    heading: str
    text: str  # the full paragraph, as shown in the viewer
    score: float  # cosine similarity, 1.0 = identical


async def search_document(
    db: AsyncSession,
    *,
    workspace_id: str,
    document_id: str,
    query_vector: list[float],
    top_k: int | None = None,
) -> list[RetrievedParagraph]:
    """Return the top-K most similar paragraphs (deduplicated), best first."""
    top_k = top_k or settings.top_k_results
    distance = Passage.embedding.cosine_distance(query_vector)
    rows = (
        await db.execute(
            select(Passage.page, Passage.paragraph, (1 - distance).label("score"))
            .where(Passage.workspace_id == workspace_id, Passage.document_id == document_id)
            .order_by(distance)
            .limit(top_k * 3)
        )
    ).all()

    best: dict[tuple[int, int], float] = {}
    for page, paragraph, score in rows:
        key = (page, paragraph)
        if key not in best:
            best[key] = float(score)
        if len(best) == top_k:
            break
    if not best:
        return []

    pages = {
        page.number: page
        for page in await db.scalars(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.number.in_({page for page, _ in best}),
            )
        )
    }
    results: list[RetrievedParagraph] = []
    for (page_number, paragraph), score in best.items():
        page = pages.get(page_number)
        if page is None or not 1 <= paragraph <= len(page.paragraphs):
            continue
        results.append(
            RetrievedParagraph(
                page=page_number,
                paragraph=paragraph,
                heading=page.heading,
                text=page.paragraphs[paragraph - 1],
                score=score,
            )
        )
    return results
