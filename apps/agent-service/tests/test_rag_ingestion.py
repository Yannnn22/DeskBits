from desktopmate_agent.rag.chunking import MarkdownChunker
from desktopmate_agent.rag.ingestion import LocalDocumentIngestionService
from desktopmate_agent.rag.loaders import TextDocumentLoader


def test_text_document_loader_reads_markdown_with_stable_metadata(tmp_path):
    note = tmp_path / "agent.md"
    note.write_text("# Agent\n\nRAG 让回答更贴近用户资料。", encoding="utf-8")
    loader = TextDocumentLoader()

    document = loader.load(note)

    assert document.source_path == str(note)
    assert document.title == "agent"
    assert document.text == "# Agent\n\nRAG 让回答更贴近用户资料。"
    assert document.metadata["extension"] == ".md"
    assert len(document.document_id) == 40


def test_markdown_chunker_keeps_heading_metadata_and_respects_size():
    document = TextDocumentLoader().load_text(
        text=(
            "# DesktopMate\n\n"
            "桌面助手需要理解用户自己的资料。\n\n"
            "## RAG\n\n"
            "文档切分会影响召回率。混合检索能兼顾关键词和语义。重排能提升命中率。"
        ),
        source_path="/tmp/desktopmate.md",
    )
    chunker = MarkdownChunker(chunk_size=36, overlap=8)

    chunks = chunker.split(document)

    assert len(chunks) >= 2
    assert all(len(chunk.text) <= 36 for chunk in chunks)
    assert chunks[0].metadata["heading_path"] == ["DesktopMate"]
    assert chunks[-1].metadata["heading_path"] == ["DesktopMate", "RAG"]
    assert chunks[-1].document_id == document.document_id
    assert chunks[-1].chunk_id.startswith(f"{document.document_id}:")


def test_local_document_ingestion_service_scans_supported_files(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "b.txt").write_text("第二份资料", encoding="utf-8")
    (tmp_path / "notes" / "a.md").write_text("# 第一份\n\nRAG ingestion", encoding="utf-8")
    (tmp_path / "notes" / "skip.pdf").write_text("unsupported", encoding="utf-8")
    service = LocalDocumentIngestionService(
        loader=TextDocumentLoader(),
        chunker=MarkdownChunker(chunk_size=80, overlap=0),
    )

    chunks = service.ingest_directory(tmp_path / "notes")

    assert [chunk.metadata["title"] for chunk in chunks] == ["a", "b"]
    assert all(chunk.metadata["source_path"].endswith((".md", ".txt")) for chunk in chunks)
