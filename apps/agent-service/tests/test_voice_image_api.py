"""语音转写与图片附件 API 测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.attachments.store import AttachmentStore
from desktopmate_agent.main import create_app
from desktopmate_agent.speech.stt import TranscriptionResult

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


class FakeSTT:
    def __init__(self, text: str = "你好，桌面助手") -> None:
        self.text = text
        self.calls: list[bytes] = []

    def transcribe(self, wav_bytes: bytes) -> TranscriptionResult:
        self.calls.append(wav_bytes)
        return TranscriptionResult(text=self.text, language="zh", duration=1.2)


def test_transcribe_returns_text(tmp_path: Path, monkeypatch) -> None:
    fake = FakeSTT()
    from desktopmate_agent import main as main_module

    original = main_module.WhisperSTT
    main_module.WhisperSTT = lambda settings: fake  # type: ignore[assignment]
    try:
        client = TestClient(create_app(settings=_settings(tmp_path)))
        response = client.post(
            "/api/transcribe",
            headers=AUTH_HEADERS,
            files={"audio": ("recording.wav", b"RIFF-fake-wav", "audio/wav")},
        )
    finally:
        main_module.WhisperSTT = original
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "你好，桌面助手"
    assert data["language"] == "zh"
    assert data["duration"] == 1.2
    assert fake.calls[0] == b"RIFF-fake-wav"


def test_transcribe_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=_settings(tmp_path)))
    response = client.post(
        "/api/transcribe",
        files={"audio": ("recording.wav", b"RIFF", "audio/wav")},
    )
    assert response.status_code == 401


def test_upload_and_read_image(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=_settings(tmp_path)))
    png_bytes = b"\x89PNG\r\n\x1a\nfake-image-data"

    upload = client.post(
        "/api/uploads",
        headers=AUTH_HEADERS,
        files={"files": ("photo.png", png_bytes, "image/png")},
    )
    assert upload.status_code == 200
    items = upload.json()["attachments"]
    assert len(items) == 1
    attachment_id = items[0]["id"]

    read = client.get(f"/api/attachments/{attachment_id}?token=test-token")
    assert read.status_code == 200
    assert read.content == png_bytes
    assert read.headers["content-type"] == "image/png"
    assert read.headers["content-disposition"].startswith("inline;")

    location = client.get(f"/api/attachments/{attachment_id}/location", headers=AUTH_HEADERS)
    assert location.status_code == 200
    assert Path(location.json()["path"]).read_bytes() == png_bytes

    denied = client.get(f"/api/attachments/{attachment_id}")
    assert denied.status_code == 401


def test_upload_rejects_unsupported_type(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=_settings(tmp_path)))
    response = client.post(
        "/api/uploads",
        headers=AUTH_HEADERS,
        files={"files": ("notes.bin", b"\x00\x01", "application/x-msdownload")},
    )
    assert response.status_code == 400


def test_attachment_store_survives_reload(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "uploads")
    record = store.save(filename="a.png", mime="image/png", content=b"\x89PNG-data")
    reloaded = AttachmentStore(tmp_path / "uploads")
    assert reloaded.get(record.id) is not None
    assert reloaded.path_for(record.id).read_bytes() == b"\x89PNG-data"


def _settings(tmp_path: Path):
    from desktopmate_agent.config import Settings

    return Settings(
        trace_dir=tmp_path / "traces",
        allowed_roots=str(tmp_path),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
        uploads_dir=tmp_path / "uploads",
        whisper_model_dir=tmp_path / "whisper-models",
    )


async def test_agent_rejects_image_when_model_lacks_vision(tmp_path: Path, monkeypatch) -> None:
    """模型不支持视觉时，带图消息给出明确降级提示（不调用模型）。"""
    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.attachments.store import AttachmentStore
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry
    from desktopmate_agent.trace.store import JsonlTraceStore

    uploads = tmp_path / "uploads"
    store = AttachmentStore(uploads)
    record = store.save(filename="pic.png", mime="image/png", content=b"\x89PNG-fake")

    provider = FakeModelProvider(response_text="图里有一只猫。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        attachment_store=store,
        session_store=None,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="看看这张图",
            session_id="s-vision-1",
            attachments=[record.id],
        )
    ]
    text_events = [e for e in events if e.type == "agent_message"]
    assert text_events, "expected agent_message event"
    assert "不支持图片识别" in text_events[0].payload.get("text", "")
    # 视觉不支持时不应调用模型
    assert provider.prompts == []


async def test_agent_sends_image_content_when_model_supports_vision(tmp_path: Path, monkeypatch) -> None:
    """模型支持视觉时，带图消息构建多模态 content（text + image_url）。"""
    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.attachments.store import AttachmentStore
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry
    from desktopmate_agent.trace.store import JsonlTraceStore

    class VisionFake(FakeModelProvider):
        def supports_vision(self) -> bool:
            return True

    uploads = tmp_path / "uploads"
    store = AttachmentStore(uploads)
    record = store.save(filename="pic.png", mime="image/png", content=b"\x89PNG-fake")

    provider = VisionFake(response_text="图里有一只猫。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        attachment_store=store,
        session_store=None,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="看看这张图",
            session_id="s-vision-2",
            attachments=[record.id],
        )
    ]
    text_events = [e for e in events if e.type == "agent_message"]
    assert text_events
    assert "图里有一只猫。" in text_events[0].payload.get("text", "")
    # complete_content 收到包含 data URL 图片的多模态 content
    assert provider.prompts, "expected multimodal content to reach provider"
    assert "data:image/png;base64," in provider.prompts[-1]


def test_model_supports_vision_gpt5_and_deepseek_off() -> None:
    """gpt-5.x 判定支持视觉；deepseek 域强制关闭。"""
    from desktopmate_agent.models.provider import _model_supports_vision

    assert _model_supports_vision("gpt-5.4-mini", base_url="https://api.mytools123.top/v1") is True
    assert _model_supports_vision("gpt-5.5", base_url="https://api.mytools123.top/v1") is True
    assert _model_supports_vision("gpt-5.6-luna", base_url=None) is True
    assert _model_supports_vision("deepseek-v4-flash", base_url="https://api.deepseek.com") is False
    assert _model_supports_vision("gpt-5.5", base_url="https://api.deepseek.com") is False


def test_attachment_store_cross_instance_visibility(tmp_path: Path) -> None:
    """上传路由与 agent 服务是不同 AttachmentStore 实例时，get 仍能读到新附件。

    回归：main.py 曾为上传路由与 AgentService 各建一个实例，后者的内存索引
    是旧的，导致带图聊天静默丢图。磁盘重载兜底后跨实例可见。
    """
    from desktopmate_agent.attachments.store import AttachmentStore

    uploads = tmp_path / "uploads"
    agent_store = AttachmentStore(uploads)  # 先建：内存索引为空
    upload_store = AttachmentStore(uploads)  # 后建：上传走这个实例
    record = upload_store.save(filename="pic.png", mime="image/png", content=b"\x89PNG-fake")

    assert agent_store.get(record.id) is not None
    assert agent_store.path_for(record.id) is not None


def test_upload_document_attachment_and_inject_into_prompt(tmp_path: Path) -> None:
    """文档附件：上传成功 → chat 时注入 system prompt（方案 B 直接对话）。"""
    from desktopmate_agent.agent.graph import AgentGraph
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry

    client = TestClient(create_app(settings=_settings(tmp_path)))
    content = "# 项目说明\n\nDeskBits 是一个本地桌面 Agent 助手。"
    response = client.post(
        "/api/uploads",
        headers=AUTH_HEADERS,
        files={"files": ("notes.md", content.encode(), "text/markdown")},
    )
    assert response.status_code == 200
    attachment = response.json()["attachments"][0]
    assert attachment["filename"] == "notes.md"
    assert attachment["mime"] == "text/markdown"

    # 直接验证 service 层的解析：模拟 _resolve_document_contents 等价行为
    store = AttachmentStore(_settings(tmp_path).uploads_dir)
    text = store.content_text(attachment["id"])
    assert "DeskBits 是一个本地桌面 Agent 助手" in text

    # graph 注入：构造 document_contexts 验证 prompt 含附件段
    graph = AgentGraph(
        model_provider=FakeModelProvider("ok"),
        tool_registry=ToolRegistry(),
    )
    prompt = graph._build_prompt(
        user_text="这个项目是做什么的",
        memory_context=[],
        knowledge_context=[],
        document_contexts=[{"filename": "notes.md", "text": text}],
    )
    assert "<attachments>" in prompt
    assert "[文件: notes.md]" in prompt
    assert "DeskBits 是一个本地桌面 Agent 助手" in prompt


def test_document_attachment_rejects_oversize(tmp_path: Path) -> None:
    from desktopmate_agent.attachments.store import MAX_DOCUMENT_BYTES

    client = TestClient(create_app(settings=_settings(tmp_path)))
    big = b"x" * (MAX_DOCUMENT_BYTES + 1)
    response = client.post(
        "/api/uploads",
        headers=AUTH_HEADERS,
        files={"files": ("big.md", big, "text/markdown")},
    )
    assert response.status_code == 400


def test_document_attachment_does_not_enter_multimodal_path(tmp_path: Path) -> None:
    """文档附件只进 document_contexts，不进 image_contents（无视觉模型不误报）。"""
    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry
    from desktopmate_agent.trace.store import JsonlTraceStore

    store = AttachmentStore(tmp_path / "uploads")
    record = store.save(
        filename="notes.md",
        mime="text/markdown",
        content="# Notes\n\nDeskBits 是本地 Agent。".encode("utf-8"),
    )
    service = AgentService(
        model_provider=FakeModelProvider("ok"),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        attachment_store=store,
    )
    docs = service._resolve_document_contents([record.id])
    imgs = service._resolve_image_contents([record.id])

    assert len(docs) == 1
    assert docs[0]["filename"] == "notes.md"
    assert imgs == []  # 文档不应触发多模态路径
