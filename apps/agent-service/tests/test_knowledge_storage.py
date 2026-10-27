from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        auth_token=AUTH_TOKEN,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def upload(client: TestClient, filename: str, content: bytes) -> dict:
    response = client.post(
        "/api/knowledge/documents",
        files={"file": (filename, content)},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    return response.json()


def test_upload_saves_originals_and_extracted(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))

    doc = upload(client, "notes.md", "# Notes\n内容一".encode("utf-8"))

    originals = list((settings.knowledge_dir / "originals").glob("*"))
    extracted = list((settings.knowledge_dir / "extracted").glob("*.txt"))
    assert len(originals) == 1
    assert originals[0].name == f"{doc['id']}.md"
    assert originals[0].read_text(encoding="utf-8") == "# Notes\n内容一"
    assert len(extracted) == 1
    assert "内容一" in extracted[0].read_text(encoding="utf-8")
    assert doc["original_path"] is not None
    assert doc["extracted_path"] is not None
    assert doc["sha256"] is not None
    assert doc["mime_type"] == "text/markdown"


def test_upload_records_chunks(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))

    doc = upload(client, "long.md", ("# 长文\n\n" + "内容片段 " * 200).encode("utf-8"))

    assert doc["chunk_count"] >= 2
    import sqlite3

    with sqlite3.connect(settings.knowledge_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc["id"],),
        ).fetchall()
    assert len(rows) == doc["chunk_count"]
    assert rows[0]["token_count"] > 0
    assert "内容片段" in rows[0]["text"]


def test_upload_duplicate_content_rejected(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    content = "# 去重测试\n内容".encode("utf-8")
    upload(client, "first.md", content)

    response = client.post(
        "/api/knowledge/documents",
        files={"file": ("second.md", content)},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert "重复导入" in response.json()["error"]["message"]


def test_upload_unsupported_doc_mentions_supported_types(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.post(
        "/api/knowledge/documents",
        files={"file": ("old.doc", b"legacy word")},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert "不支持的文件类型: .doc" in response.json()["error"]["message"]
    assert ".md" in response.json()["error"]["message"]


def test_delete_removes_files_and_chunks(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    doc = upload(client, "notes.md", "# Notes\n内容".encode("utf-8"))
    original = settings.knowledge_dir / "originals" / f"{doc['id']}.md"
    extracted = settings.knowledge_dir / "extracted" / f"{doc['id']}.txt"
    assert original.exists()

    deleted = client.delete(f"/api/knowledge/documents/{doc['id']}", headers=AUTH_HEADERS)

    assert deleted.json() == {"deleted": True}
    assert not original.exists()
    assert not extracted.exists()
    import sqlite3

    with sqlite3.connect(settings.knowledge_db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
            (doc["id"],),
        ).fetchone()[0]
    assert count == 0
