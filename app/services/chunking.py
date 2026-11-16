"""Split parsed pages into passages for embedding.

Every passage belongs to exactly one paragraph, so a retrieved passage always maps back to a
(page, paragraph) citation. Paragraphs longer than CHUNK_SIZE characters are split into
overlapping windows (CHUNK_OVERLAP characters) that share the same citation target.
"""

from dataclasses import dataclass

from app.core.config import settings
from app.services.parsing import ParsedPage


@dataclass(frozen=True)
class Chunk:
    page: int
    paragraph: int
    text: str
    heading: str

    @property
    def embedding_text(self) -> str:
        # Section headings as a prefix improve retrieval precision.
        return f"{self.heading}\n{self.text}" if self.heading else self.text


def split_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        windows.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = end - overlap
        # Start the next window on a word boundary.
        space = text.find(" ", next_start, end)
        start = space + 1 if space != -1 else next_start
    return [w for w in windows if w]


def chunk_pages(pages: list[ParsedPage]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        for index, paragraph in enumerate(page.paragraphs, start=1):
            for piece in split_text(paragraph, settings.chunk_size, settings.chunk_overlap):
                chunks.append(Chunk(page=page.number, paragraph=index, text=piece, heading=page.heading))
    return chunks
