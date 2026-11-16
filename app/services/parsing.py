"""Parse uploaded files into pages of paragraphs (API_CONTRACT.md section 3, Page).

Paragraph order is fixed at parse time: citations point at `paragraphs[paragraph - 1]`.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

WORDS_PER_PAGE = 400
_MAX_HEADING_CHARS = 120


class ParseError(Exception):
    """A plain-language reason the document couldn't be parsed (shown to the user)."""


@dataclass
class ParsedPage:
    number: int
    heading: str
    paragraphs: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _finalize(pages: list[ParsedPage]) -> list[ParsedPage]:
    """Drop empty pages, renumber 1..n, and default missing headings to "Page {n}"."""
    result: list[ParsedPage] = []
    for page in pages:
        paragraphs = [p for p in (_clean(p) for p in page.paragraphs) if p]
        if not paragraphs:
            continue
        number = len(result) + 1
        heading = _clean(page.heading) or f"Page {number}"
        if re.fullmatch(r"Page \d+", heading):
            heading = f"Page {number}"
        result.append(ParsedPage(number=number, heading=heading, paragraphs=paragraphs))
    if not result:
        raise ParseError("We couldn't find any readable text in this document.")
    return result


def _split_by_words(paragraphs: list[str], heading: str = "") -> list[ParsedPage]:
    """Group paragraphs into pages of roughly WORDS_PER_PAGE words."""
    pages: list[ParsedPage] = []
    current: list[str] = []
    words = 0
    for paragraph in paragraphs:
        if current and words + _word_count(paragraph) > WORDS_PER_PAGE:
            pages.append(ParsedPage(0, heading if not pages else "", current))
            current, words = [], 0
        current.append(paragraph)
        words += _word_count(paragraph)
    if current:
        pages.append(ParsedPage(0, heading if not pages else "", current))
    return pages


# --- PDF ---------------------------------------------------------------------


def _looks_like_heading(text: str) -> bool:
    return 0 < len(text) <= _MAX_HEADING_CHARS and not text.endswith((".", ",", ";", ":")) and _word_count(text) <= 14


def parse_pdf(path: Path) -> list[ParsedPage]:
    import pymupdf

    try:
        document = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ParseError("This PDF appears to be damaged and couldn't be opened.") from exc
    with document:
        if document.needs_pass or document.is_encrypted:
            raise ParseError("This PDF is password-protected.")
        pages: list[ParsedPage] = []
        for index, page in enumerate(document, start=1):
            blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0]  # 6 = block type, 0 = text
            texts = [_clean(b[4]) for b in blocks]
            texts = [t for t in texts if t and not re.fullmatch(r"\d{1,4}", t)]  # drop bare page numbers
            heading = ""
            if texts and _looks_like_heading(texts[0]) and len(texts) > 1:
                heading = texts.pop(0)
            pages.append(ParsedPage(index, heading, texts))
    if all(not p.paragraphs for p in pages):
        raise ParseError("This PDF has no selectable text (it may be a scanned image).")
    return _finalize(pages)


# --- DOCX --------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_heading_level(paragraph) -> int | None:  # noqa: ANN001
    style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
    if style == "title":
        return 0
    match = re.match(r"heading (\d)", style)
    return int(match.group(1)) if match else None


def _docx_has_page_break(paragraph) -> bool:  # noqa: ANN001
    if paragraph.paragraph_format.page_break_before:
        return True
    return any(br.get(f"{_W}type") == "page" for br in paragraph._element.iter(f"{_W}br"))


def parse_docx(path: Path) -> list[ParsedPage]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParseError("This Word document appears to be damaged or password-protected.") from exc

    # Walk the body in order: (text, heading_level, page_break_before)
    items: list[tuple[str, int | None, bool]] = []
    for child in document.element.body.iterchildren():
        if child.tag == f"{_W}p":
            paragraph = Paragraph(child, document)
            items.append((paragraph.text, _docx_heading_level(paragraph), _docx_has_page_break(paragraph)))
        elif child.tag == f"{_W}tbl":
            table = Table(child, document)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    text = _clean(cell.text)
                    if text and (not cells or cells[-1] != text):  # merged cells repeat their text
                        cells.append(text)
                if cells:
                    items.append((" | ".join(cells), None, False))

    levels = [level for text, level, _brk in items if level is not None and _clean(text)]
    top_level = min(levels) if levels else None
    has_breaks = any(brk for _text, _level, brk in items)

    if top_level is None and not has_breaks:
        return _finalize(_split_by_words([text for text, _, _ in items]))

    pages: list[ParsedPage] = [ParsedPage(0, "")]
    for text, level, page_break in items:
        text = _clean(text)
        current = pages[-1]
        if page_break and current.paragraphs:
            pages.append(ParsedPage(0, ""))
            current = pages[-1]
        if level is not None and text:
            if level == top_level:
                if current.paragraphs or current.heading:
                    pages.append(ParsedPage(0, text))
                else:
                    current.heading = text
                continue
            if not current.heading:
                current.heading = text
                continue
        if text:
            current.paragraphs.append(text)
    return _finalize(pages)


# --- Markdown & plain text ---------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _strip_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", "", text)  # list markers
    text = re.sub(r"^\s{0,3}>\s?", "", text)  # blockquotes
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    return text.strip()


def _markdown_blocks(source: str) -> list[tuple[int | None, str]]:
    """Return (heading_level or None, text) blocks. Paragraphs end at blank lines; list items stand alone."""
    blocks: list[tuple[int | None, str]] = []
    buffer: list[str] = []
    in_code = False

    def flush() -> None:
        if buffer:
            blocks.append((None, " ".join(buffer)))
            buffer.clear()

    for raw_line in source.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith(("```", "~~~")):
            in_code = not in_code
            flush()
            continue
        if in_code:
            if line.strip():
                buffer.append(line.strip())
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append((len(heading.group(1)), _strip_markdown(heading.group(2))))
        elif not line.strip() or re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", line):
            flush()
        elif re.match(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", line):
            flush()
            buffer.append(_strip_markdown(line))
        else:
            buffer.append(_strip_markdown(line) if not buffer else line.strip())
    flush()
    return [(level, _clean(text)) for level, text in blocks if _clean(text)]


def _pages_from_blocks(blocks: list[tuple[int | None, str]]) -> list[ParsedPage]:
    levels = [level for level, _ in blocks if level is not None]
    if not levels:
        return _split_by_words([text for _, text in blocks])
    top_level = min(levels)
    pages: list[ParsedPage] = [ParsedPage(0, "")]
    for level, text in blocks:
        current = pages[-1]
        if level == top_level:
            if current.paragraphs:
                pages.append(ParsedPage(0, text))
            else:
                current.heading = text
        elif level is not None and not current.heading:
            current.heading = text
        elif level is not None:
            current.paragraphs.append(text)
        else:
            current.paragraphs.append(text)
    # Very long sections are split further every ~WORDS_PER_PAGE words.
    expanded: list[ParsedPage] = []
    for page in pages:
        if sum(_word_count(p) for p in page.paragraphs) > WORDS_PER_PAGE * 2:
            parts = _split_by_words(page.paragraphs, page.heading)
            for part in parts[1:]:
                part.heading = f"{page.heading} (cont.)" if page.heading else ""
            expanded.extend(parts)
        else:
            expanded.append(page)
    return expanded


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_markdown(path: Path) -> list[ParsedPage]:
    return _finalize(_pages_from_blocks(_markdown_blocks(_read_text(path))))


def parse_text(path: Path) -> list[ParsedPage]:
    source = _read_text(path).replace("\r\n", "\n")
    paragraphs = [_clean(p) for p in re.split(r"\n\s*\n", source)]
    paragraphs = [p for p in paragraphs if p]
    # Single-line paragraphs that look like headings ("INTRODUCTION", "1. Scope") start new pages.
    blocks: list[tuple[int | None, str]] = []
    for paragraph in paragraphs:
        is_heading = (
            _looks_like_heading(paragraph)
            and _word_count(paragraph) <= 8
            and (paragraph.isupper() or paragraph.istitle() or re.match(r"^\d+(\.\d+)*\s", paragraph))
        )
        blocks.append((1 if is_heading else None, paragraph))
    heading_count = sum(1 for level, _ in blocks if level)
    if heading_count < 2:
        blocks = [(None, text) for _, text in blocks]
    return _finalize(_pages_from_blocks(blocks))


PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "md": parse_markdown, "txt": parse_text}


def parse_document(path: Path, file_format: str) -> list[ParsedPage]:
    try:
        return PARSERS[file_format](path)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError("We couldn't read this file. It may be damaged or in an unexpected format.") from exc
