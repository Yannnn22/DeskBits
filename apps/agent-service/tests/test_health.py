from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app


AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def test_invoke_can_use_default_search_tool(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    settings = Settings(
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    response = client.post(
        "/api/chat/invoke",
        json={"text": "/search note", "mode": "agent"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "找到 1 个文件"


def test_invoke_can_use_default_read_file_tool(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    settings = Settings(
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    response = client.post(
        "/api/chat/invoke",
        json={"text": f"/read {note}", "mode": "agent"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "读取文件成功"


def test_invoke_can_use_default_knowledge_search_tool(tmp_path, tmp_allowed_root):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    settings = Settings(
        trace_dir=tmp_path / "traces",
        memory_dir=tmp_path / "memory",
        knowledge_dir=knowledge_dir,
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))
    # S7：统一摄入源为 SQLite——上传走 API
    uploaded = client.post(
        "/api/knowledge/documents",
        headers=AUTH_HEADERS,
        files={"file": ("milvus.md", "# Milvus 配置\n\n先启动 Docker，再创建 collection。".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text

    response = client.post(
        "/api/chat/invoke",
        json={"text": "/knowledge 怎么配置 Milvus"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"]  # 模型基于检索结果的综合回答
    run_id = response.json()["run_id"]
    events = client.get(f"/api/runs/{run_id}/trace", headers=AUTH_HEADERS).json()["events"]
    tool_event = next(event for event in events if event["type"] == "tool_call_finished")
    matches = tool_event["payload"]["result"]["data"]["matches"]
    assert len(matches) == 1
    # 检索正文在 trace 中会被脱敏（安全设计），这里只验证命中数量与来源
    assert matches[0]["source_path"]


def test_invoke_can_refresh_default_knowledge_index(tmp_path, tmp_allowed_root):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    settings = Settings(
        trace_dir=tmp_path / "traces",
        memory_dir=tmp_path / "memory",
        knowledge_dir=knowledge_dir,
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))
    # S7：统一摄入源为 SQLite——上传走 API
    uploaded = client.post(
        "/api/knowledge/documents",
        headers=AUTH_HEADERS,
        files={"file": ("bm25.md", "# BM25\n\nBM25 可以提升关键词召回。".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text

    response = client.post(
        "/api/chat/invoke",
        json={"text": "/knowledge-refresh", "mode": "agent"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "知识库索引已刷新，当前 1 个片段"


def test_create_app_can_use_redis_memory_backend(tmp_path, tmp_allowed_root, monkeypatch):
    calls = []

    class FakeRedisMemoryStore:
        def __init__(self, *, redis_url: str, key_prefix: str) -> None:
            calls.append({"redis_url": redis_url, "key_prefix": key_prefix})

        def read_context(self, **kwargs):
            return []

    monkeypatch.setattr("desktopmate_agent.main.RedisMemoryStore", FakeRedisMemoryStore)
    settings = Settings(
        trace_dir=tmp_path / "traces",
        memory_backend="redis",
        redis_url="redis://127.0.0.1:16379/1",
        redis_memory_key_prefix="desktopmate:test:memory",
        knowledge_dir=tmp_path / "knowledge",
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )

    create_app(trace_dir=tmp_path, settings=settings)

    assert calls == [
        {
            "redis_url": "redis://127.0.0.1:16379/1",
            "key_prefix": "desktopmate:test:memory",
        }
    ]


def test_create_app_uses_configured_embedding_provider(tmp_path, tmp_allowed_root, monkeypatch):
    calls = []

    class RecordingEmbeddingProvider:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    def fake_create_embedding_provider(**kwargs):
        calls.append(kwargs)
        return RecordingEmbeddingProvider()

    monkeypatch.setattr(
        "desktopmate_agent.main.create_embedding_provider",
        fake_create_embedding_provider,
        raising=False,
    )
    settings = Settings(
        trace_dir=tmp_path / "traces",
        memory_dir=tmp_path / "memory",
        knowledge_dir=tmp_path / "knowledge",
        allowed_roots=str(tmp_allowed_root),
        embedding_provider="openai",
        embedding_api_key="test-key",
        embedding_base_url="https://example.test/v1",
        embedding_model="text-embedding-3-small",
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )

    create_app(trace_dir=tmp_path, settings=settings)

    assert calls == [
        {
            "provider_name": "openai",
            "dimensions": 128,
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "model": "text-embedding-3-small",
        }
    ]


def test_invoke_can_remember_user_fact(tmp_path, tmp_allowed_root):
    settings = Settings(
        trace_dir=tmp_path / "traces",
        memory_dir=tmp_path / "memory",
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    response = client.post(
        "/api/chat/invoke",
        json={"text": "/remember preferred_language 中文", "session_id": "session_1"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "已记住 preferred_language。"


def test_create_app_can_enable_model_intent_router(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    settings = Settings(
        allowed_roots=str(tmp_allowed_root),
        intent_router="model",
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    response = client.post(
        "/api/chat/invoke",
        json={"text": "帮我找一下 note 文件"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "收到。"


def test_health_returns_ready(no_api_settings):
    client = TestClient(create_app(settings=no_api_settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["service"] == "desktopmate-agent-service"
    assert "version" in response.json()


def test_invoke_returns_agent_response(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))

    response = client.post("/api/chat/invoke", json={"text": "hello"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "收到。"
    assert data["session_id"].startswith("session_")
    assert data["run_id"].startswith("run_")


# --- S8: API index + readiness ---


def test_api_index_root(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "desktopmate-agent-service"
    assert "version" in body
    assert body["api"]["health"] == "/api/health"
    assert body["api"]["index"] == "/api/knowledge/index/status"
    assert body["api"]["speech"] == "/api/speech/status"


def test_api_health_readiness(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["version"] == "0.1.0"
    components = body["components"]
    assert components["db"] == "ok"
    assert components["settings"] == "ok"
    # 无 API key 时模型走 fake，仍视为可用
    model = components["model_provider"]
    assert model["status"] == "ok"
    assert model["provider"] in ("fake", "openai", "deepseek")
    speech = components["speech"]
    assert "status" in speech
    assert "model_cached" in speech
