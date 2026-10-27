from pathlib import Path

from desktopmate_agent.rag.chunking import MarkdownChunker
from desktopmate_agent.rag.loaders import TextDocumentLoader
from desktopmate_agent.rag.models import KnowledgeChunk


class LocalDocumentIngestionService:
    def __init__(self, *, loader: TextDocumentLoader, chunker: MarkdownChunker) -> None:
        self.loader = loader
        self.chunker = chunker

    def ingest_directory(self, root: Path) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self.loader.supported_extensions:
                continue
            document = self.loader.load(path)
            chunks.extend(self.chunker.split(document))
        return chunks
