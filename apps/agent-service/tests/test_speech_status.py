"""S5 语音体验增强：/api/speech/status + preload 任务状态机。"""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.speech.stt import (
    PRELOAD_STATUS_FAILED,
    PRELOAD_STATUS_READY,
    WhisperSTT,
)

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "auth_token": "test-token",
        "whisper_model": "tiny",
        "whisper_model_dir": tmp_path / "whisper",
        "whisper_lang": "auto",
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- WhisperSTT.status / 缓存探测 ---


def test_status_reports_fields_and_not_cached(tmp_path):
    stt = WhisperSTT(settings=make_settings(tmp_path))
    status = stt.status()
    assert status["enabled"] is True
    assert status["model"] == "tiny"
    assert status["device"] == "cpu"
    assert status["language"] == "auto"
    assert status["model_cached"] is False
    assert status["model_dir"] == str(tmp_path / "whisper")


def test_status_model_cached_detection(tmp_path):
    settings = make_settings(tmp_path)
    # 构造 fake 缓存：{model_dir}/hub/models--Systran--faster-whisper-tiny/snapshots/x/model.bin
    snapshot = (
        tmp_path
        / "whisper"
        / "hub"
        / "models--Systran--faster-whisper-tiny"
        / "snapshots"
        / "abc123"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"fake")
    stt = WhisperSTT(settings=settings)
    assert stt.status()["model_cached"] is True


def test_status_model_cached_local_file(tmp_path):
    model_file = tmp_path / "whisper-tiny.bin"
    model_file.write_bytes(b"fake")
    stt = WhisperSTT(settings=make_settings(tmp_path, whisper_model=str(model_file)))
    assert stt.status()["model_cached"] is True


# --- preload 任务状态机 ---


def test_preload_job_reaches_ready(tmp_path, monkeypatch):
    stt = WhisperSTT(settings=make_settings(tmp_path))
    monkeypatch.setattr(stt, "_ensure_model", lambda: None)  # 不真实下载

    job_id = stt.preload()
    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        status = stt.preload_status(job_id)
        if status and status["status"] in (PRELOAD_STATUS_READY, PRELOAD_STATUS_FAILED):
            break
        time.sleep(0.02)
    assert status is not None
    assert status["status"] == PRELOAD_STATUS_READY


def test_preload_job_failed_records_error(tmp_path, monkeypatch):
    stt = WhisperSTT(settings=make_settings(tmp_path))

    def _boom() -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(stt, "_ensure_model", _boom)
    job_id = stt.preload()
    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        status = stt.preload_status(job_id)
        if status and status["status"] == PRELOAD_STATUS_FAILED:
            break
        time.sleep(0.02)
    assert status is not None
    assert status["status"] == PRELOAD_STATUS_FAILED
    assert "download failed" in status["error"]


def test_preload_status_unknown_job_returns_none(tmp_path):
    stt = WhisperSTT(settings=make_settings(tmp_path))
    assert stt.preload_status("preload_nope") is None


# --- API ---


def test_speech_api_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.get("/api/speech/status").status_code == 401
    assert client.post("/api/speech/preload").status_code == 401
    assert client.get("/api/speech/preload/x").status_code == 401


def test_speech_status_endpoint(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    resp = client.get("/api/speech/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["model"] == "tiny"
    assert body["model_cached"] is False


def test_speech_preload_endpoint(tmp_path, monkeypatch):
    # 避免真实下载：把所有 WhisperSTT._ensure_model 换成 no-op
    monkeypatch.setattr(WhisperSTT, "_ensure_model", lambda self: None)
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    created = client.post("/api/speech/preload", headers=AUTH_HEADERS)
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    assert job_id.startswith("preload_")

    # 轮询直到 ready
    status = None
    for _ in range(100):
        resp = client.get(f"/api/speech/preload/{job_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("ready", "failed"):
            break
        time.sleep(0.02)
    assert status == "ready"

    unknown = client.get("/api/speech/preload/preload_nope", headers=AUTH_HEADERS)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "job_not_found"
