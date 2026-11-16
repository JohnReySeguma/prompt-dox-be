"""Convert the frontend mock files (document/documents.ts, document/knowledge.ts)
into app/seed/seed_data.json, which the backend uses to seed the demo template.

Run from the repo root:  python scripts/generate_seed.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS_TS = ROOT / "document" / "documents.ts"
KNOWLEDGE_TS = ROOT / "document" / "knowledge.ts"
OUTPUT = ROOT / "app" / "seed" / "seed_data.json"


def _extract_literal(source: str, marker: str) -> str:
    """Return the bracketed literal ([...] or {...}) that follows `marker`."""
    start = source.index(marker) + len(marker)
    while source[start] not in "[{":
        start += 1
    opening = source[start]
    closing = "]" if opening == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ValueError(f"Unterminated literal after {marker!r}")


def _ts_to_json(literal: str) -> object:
    """Convert a TS object/array literal (double-quoted strings only) to Python."""
    out: list[str] = []
    index = 0
    in_string = False
    while index < len(literal):
        char = literal[index]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(literal[index + 1])
                index += 1
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        # Outside strings: drop `pages(` calls, quote bare keys, strip numeric separators.
        if literal.startswith("pages(", index):
            index += len("pages(")
            # Remember to drop the matching ")" later.
            out.append("\x00PAGES\x00")
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", literal[index:])
        if match and (not out or re.search(r"[{,\s]$", "".join(out[-3:]) or " ")):
            out.append(f'"{match.group(1)}":')
            index += match.end()
            continue
        match = re.match(r"\d[\d_]*", literal[index:])
        if match:
            out.append(match.group(0).replace("_", ""))
            index += match.end()
            continue
        out.append(char)
        index += 1

    text = "".join(out)
    # Each pages([...]) call: the marker precedes "[", and a ")" follows the matching "]".
    while "\x00PAGES\x00" in text:
        position = text.index("\x00PAGES\x00")
        text = text[:position] + '{"__pages__":' + text[position + len("\x00PAGES\x00") :]
        literal_end = position + len('{"__pages__":') + len(_extract_literal(text[position:], '{"__pages__":'))
        close = text.index(")", literal_end)
        text = text[:literal_end] + "}" + text[close + 1 :]
    text = re.sub(r",(\s*[\]}])", r"\1", text)
    return json.loads(text)


def _expand_pages(value: object) -> object:
    if isinstance(value, dict):
        if set(value) == {"__pages__"}:
            return [
                {"number": number, "heading": heading, "paragraphs": paragraphs}
                for number, (heading, paragraphs) in enumerate(value["__pages__"], start=1)
            ]
        return {key: _expand_pages(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_pages(item) for item in value]
    return value


def main() -> None:
    documents_source = DOCUMENTS_TS.read_text(encoding="utf-8")
    knowledge_source = KNOWLEDGE_TS.read_text(encoding="utf-8")

    documents = _expand_pages(_ts_to_json(_extract_literal(documents_source, "MOCK_DOCUMENTS: DocumentItem[] =")))
    knowledge = _ts_to_json(_extract_literal(knowledge_source, "MOCK_KNOWLEDGE: Record<string, DocKnowledge> ="))
    fallback = _ts_to_json(_extract_literal(knowledge_source, "const FALLBACK: MockAnswer ="))
    initial_chats = _ts_to_json(_extract_literal(knowledge_source, "INITIAL_CHATS: Record<string, ChatMessage[]> ="))
    default_suggestions = _ts_to_json(_extract_literal(knowledge_source, 'MOCK_KNOWLEDGE[docId]?.suggestions ??'))

    for document in documents:
        # The seed ships every document as ready (API_CONTRACT.md section 6).
        document["status"] = "ready"
        document["progress"] = 100

    payload = {
        "documents": documents,
        "knowledge": knowledge,
        "fallbackAnswer": fallback,
        "defaultSuggestions": default_suggestions,
        "initialChats": initial_chats,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {len(documents)} documents, {len(knowledge)} knowledge entries")


if __name__ == "__main__":
    main()
