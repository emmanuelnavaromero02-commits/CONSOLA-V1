from dataclasses import dataclass


@dataclass
class TextChunk:
    content: str
    index: int
    chunk_type: str       # 'parent' | 'child'
    parent_index: int | None = None


def _split(text: str, size: int, overlap: int) -> list[str]:
    parts, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        parts.append(text[start:end])
        if end == len(text):
            break
        start += size - overlap
    return parts


def chunk_document(
    text: str,
    parent_size: int = 3000,
    child_size: int = 600,
    parent_overlap: int = 200,
    child_overlap: int = 100,
) -> list[TextChunk]:
    text = text.strip()
    if not text:
        return []
    chunks: list[TextChunk] = []
    child_idx = 0
    for p_idx, p_text in enumerate(_split(text, parent_size, parent_overlap)):
        chunks.append(TextChunk(content=p_text, index=p_idx, chunk_type="parent"))
        for c_text in _split(p_text, child_size, child_overlap):
            chunks.append(TextChunk(
                content=c_text,
                index=child_idx,
                chunk_type="child",
                parent_index=p_idx,
            ))
            child_idx += 1
    return chunks
