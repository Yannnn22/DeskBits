import re
from collections.abc import Iterator

from desktopmate_agent.rag.models import KnowledgeChunk, KnowledgeDocument


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class MarkdownChunker:
    def __init__(self, *, chunk_size: int = 800, overlap: int = 120) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for heading_path, block, start_char in self._iter_markdown_blocks(document.text):
            for piece_start, piece_text in self._split_text(block):
                chunk_index = len(chunks)
                start = start_char + piece_start
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{document.document_id}:{chunk_index}",
                        document_id=document.document_id,
                        text=piece_text,
                        chunk_index=chunk_index,
                        start_char=start,
                        end_char=start + len(piece_text),
                        metadata={
                            **document.metadata,
                            "source_path": document.source_path,
                            "title": document.title,
                            "heading_path": heading_path,
                        },
                    )
                )
        return chunks

    def _iter_markdown_blocks(self, text: str) -> Iterator[tuple[list[str], str, int]]:
        heading_path: list[str] = []
        offset = 0
        for raw_block in re.split(r"(\n\s*\n)", text):
            if not raw_block:
                continue
            start_char = offset
            offset += len(raw_block)
            block = raw_block.strip()
            if not block:
                continue
            heading_match = HEADING_RE.match(block)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                heading_path = [*heading_path[: level - 1], title]
                continue
            yield [*heading_path], block, start_char + raw_block.find(block)

    def _split_text(self, text: str) -> Iterator[tuple[int, str]]:
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            yield start, text[start:end]
            if end == len(text):
                break
            start = max(end - self.overlap, start + 1)
