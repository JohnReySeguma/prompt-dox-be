"""Retrieval check against the expected answers in document/knowledge.ts (API_CONTRACT.md section 6).

For every suggested question of a seed document, the canned answer is picked with the same
keyword matching as the frontend mock (`findAnswer`). The paragraphs that answer cites should
appear in the TOP_K passages retrieved from the demo template workspace.

Run inside the API container:  docker compose exec api python -m scripts.eval_retrieval
"""

import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Document, Workspace
from app.services import ai
from app.services.retrieval import search_document
from app.services.seed import seed_data


def find_answer(answers: list[dict], question: str) -> dict | None:
    q = question.lower()
    best, best_score = None, 0
    for answer in answers:
        score = sum(1 for keyword in answer["keywords"] if keyword in q)
        if score > best_score:
            best, best_score = answer, score
    return best


async def main() -> None:
    data = seed_data()
    total_cited = total_hit = 0
    async with SessionLocal() as db:
        template_id = await db.scalar(select(Workspace.id).where(Workspace.is_template.is_(True)))
        if template_id is None:
            raise SystemExit("No template workspace yet; start the API first.")
        documents = {
            d.seed_key: d for d in await db.scalars(select(Document).where(Document.workspace_id == template_id))
        }
        for seed_key, knowledge in data["knowledge"].items():
            document = documents.get(seed_key)
            if document is None:
                continue
            print(f"\n{document.name}")
            for question in knowledge["suggestions"]:
                answer = find_answer(knowledge["answers"], question)
                if answer is None:
                    continue
                expected = {(c["page"], c["paragraph"]) for c in answer["citations"]}
                results = await search_document(
                    db,
                    workspace_id=template_id,
                    document_id=document.id,
                    query_vector=await ai.embed_query(question),
                )
                retrieved = {(r.page, r.paragraph) for r in results}
                hits = expected & retrieved
                total_cited += len(expected)
                total_hit += len(hits)
                top = f"{results[0].score:.2f}" if results else "-"
                print(f"  {len(hits)}/{len(expected)}  top score {top}  {question}")
    recall = total_hit / total_cited if total_cited else 0
    print(f"\nCited-paragraph recall@{settings.top_k_results}: {total_hit}/{total_cited} ({recall:.0%})")


if __name__ == "__main__":
    asyncio.run(main())
