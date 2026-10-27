from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "auth_token": AUTH_TOKEN,
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def upload(client: TestClient, filename: str, content: bytes, **form) -> dict:
    response = client.post(
        "/api/knowledge/documents",
        files={"file": (filename, content)},
        data=form or None,
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    return response.json()


def test_upload_markdown_indexes_document(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    document = upload(client, "notes.md", "# DesktopMate\n个人知识库测试内容。".encode("utf-8"))

    assert document["id"].startswith("doc_")
    assert document["name"] == "notes.md"
    assert document["type"] == "MD"
    assert document["size"] > 0
    assert document["status"] == "indexed"
    assert document["chunk_count"] >= 1
    assert document["error"] is None

    listed = client.get("/api/knowledge/documents", headers=AUTH_HEADERS).json()["documents"]
    assert [item["id"] for item in listed] == [document["id"]]


def test_upload_to_collection_and_filter(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    collection = client.post(
        "/api/knowledge/collections",
        json={"name": "Projects", "description": "项目文档"},
        headers=AUTH_HEADERS,
    ).json()

    document = upload(client, "plan.md", "# Plan\n内容".encode("utf-8"), collection_id=collection["id"])
    other = upload(client, "todo.txt", b"todo items")

    filtered = client.get(
        f"/api/knowledge/documents?collection={collection['id']}",
        headers=AUTH_HEADERS,
    ).json()["documents"]
    searched = client.get("/api/knowledge/documents?q=plan", headers=AUTH_HEADERS).json()["documents"]

    assert document["collection_id"] == collection["id"]
    assert other["collection_id"] is None
    assert [item["id"] for item in filtered] == [document["id"]]
    assert [item["id"] for item in searched] == [document["id"]]


def test_upload_unsupported_type_rejected(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.post(
        "/api/knowledge/documents",
        files={"file": ("evil.exe", b"MZ...")},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_document"


def test_upload_oversized_rejected(tmp_path):
    settings = make_settings(tmp_path, max_upload_bytes=100)
    client = TestClient(create_app(settings=settings))

    response = client.post(
        "/api/knowledge/documents",
        files={"file": ("big.md", b"x" * 1024)},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_document"


def test_delete_document(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    document = upload(client, "notes.md", b"# Hello")

    deleted = client.delete(f"/api/knowledge/documents/{document['id']}", headers=AUTH_HEADERS)
    missing = client.get(f"/api/knowledge/documents/{document['id']}", headers=AUTH_HEADERS)

    assert deleted.json() == {"deleted": True}
    assert missing.status_code == 404


def test_reindex_document(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    document = upload(client, "notes.md", b"# Hello\n" + b"word " * 200)

    reindexed = client.post(
        f"/api/knowledge/documents/{document['id']}/reindex",
        headers=AUTH_HEADERS,
    )

    assert reindexed.status_code == 200
    assert reindexed.json()["status"] == "indexed"


def test_refresh_rebuilds_index(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    refreshed = client.post("/api/knowledge/refresh", headers=AUTH_HEADERS)

    assert refreshed.status_code == 200
    assert "chunk_count" in refreshed.json()


def test_collections_crud(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    created = client.post(
        "/api/knowledge/collections",
        json={"name": "Work", "description": "工作文档"},
        headers=AUTH_HEADERS,
    )
    assert created.json()["id"].startswith("collection_")

    collection_id = created.json()["id"]
    renamed = client.patch(
        f"/api/knowledge/collections/{collection_id}",
        json={"name": "Work 2026"},
        headers=AUTH_HEADERS,
    )
    assert renamed.json()["name"] == "Work 2026"

    listed = client.get("/api/knowledge/collections", headers=AUTH_HEADERS).json()["collections"]
    assert [item["id"] for item in listed] == [collection_id]

    deleted = client.delete(f"/api/knowledge/collections/{collection_id}", headers=AUTH_HEADERS)
    assert deleted.json() == {"deleted": True}


def test_knowledge_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/api/knowledge/documents")

    assert response.status_code == 401


def test_pdf_and_docx_loaders():
    from desktopmate_agent.rag.loaders import DocxDocumentLoader, PdfDocumentLoader, TextDocumentLoader

    text_document = TextDocumentLoader().load_text(text="hello", source_path="/tmp/a.md")
    assert text_document.title == "a"

    from docx import Document as DocxDocument

    docx_bytes = BytesIO()
    docx = DocxDocument()
    docx.add_paragraph("第一段内容")
    docx.save(docx_bytes)
    path = Path("/tmp/desktopmate-test.docx")
    path.write_bytes(docx_bytes.getvalue())
    try:
        loaded = DocxDocumentLoader().load(path)
        assert "第一段内容" in loaded.text
    finally:
        path.unlink(missing_ok=True)

    pdf_path = Path("/tmp/desktopmate-test.pdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    try:
        # 无文本页应报错而不是崩溃
        import pytest

        with pytest.raises(ValueError, match="无可提取文本"):
            PdfDocumentLoader().load(pdf_path)
        # 损坏文件应抛异常
        pdf_path.write_bytes(b"not a real pdf")
        with pytest.raises(Exception):
            PdfDocumentLoader().load(pdf_path)
    finally:
        pdf_path.unlink(missing_ok=True)
