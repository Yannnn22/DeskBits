"""本地语音转写（faster-whisper / CTranslate2）。

按需加载模型：首次调用时下载（默认 Systran/faster-whisper-small，约 466MB），
之后从本地缓存加载，离线可用。模型加载较慢，模块内缓存实例避免重复加载。

S5 增加：``status()`` 报告模型/缓存状态，``preload()`` 后台触发下载+加载
（job 状态机 queued -> loading -> ready | failed），供前端在首次录音前
展示"需要下载模型"等明确状态。
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import zhconv
from faster_whisper import WhisperModel

from desktopmate_agent.config import Settings

PRELOAD_STATUS_QUEUED = "queued"
PRELOAD_STATUS_DOWNLOADING = "downloading"
PRELOAD_STATUS_LOADING = "loading"
PRELOAD_STATUS_READY = "ready"
PRELOAD_STATUS_FAILED = "failed"


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration: float | None


def simplify_chinese_text(text: str) -> str:
    """繁体中文转简体（zhconv 双向转换表，只影响汉字，其余字符原样保留）。"""
    return zhconv.convert(text, "zh-cn")


class WhisperSTT:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._model: WhisperModel | None = None
        self._lock = threading.Lock()
        self._preload_jobs: dict[str, dict[str, Any]] = {}
        self._preload_lock = threading.Lock()

    # --- 模型缓存探测 ---

    def _default_cache_root(self) -> Path:
        return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))

    def _find_cached_model(self) -> bool:
        """不加载模型，仅探测缓存中是否已有 model.bin。"""
        name = self._settings.whisper_model
        local_path = Path(name)
        if local_path.exists() and local_path.is_file():
            return True  # 本地绝对路径模型文件
        org_name = name if "/" in name else f"Systran/faster-whisper-{name}"
        roots: list[Path] = []
        if self._settings.whisper_model_dir is not None:
            roots.append(self._settings.whisper_model_dir)
        roots.append(self._default_cache_root())
        hub_dir = f"models--{org_name.replace('/', '--')}"
        for root in roots:
            hub = root / "hub" if (root / "hub").is_dir() else root
            for snapshot in (hub / hub_dir / "snapshots").glob("*"):
                if (snapshot / "model.bin").exists():
                    return True
        return False

    def status(self) -> dict[str, Any]:
        """语音能力状态：模型名/设备/语言/是否已缓存/缓存目录。"""
        return {
            "enabled": True,
            "model": self._settings.whisper_model,
            "device": self._settings.whisper_device,
            "language": self._settings.whisper_lang,
            "model_cached": self._find_cached_model(),
            "model_dir": str(self._settings.whisper_model_dir)
            if self._settings.whisper_model_dir is not None
            else str(self._default_cache_root()),
        }

    # --- 预加载任务状态机 ---

    def preload(self) -> str:
        """后台触发模型下载+加载，返回 job id。"""
        job_id = f"preload_{uuid.uuid4().hex[:12]}"
        with self._preload_lock:
            self._preload_jobs[job_id] = {"status": PRELOAD_STATUS_QUEUED, "error": None}

        def _run() -> None:
            with self._preload_lock:
                self._preload_jobs[job_id]["status"] = PRELOAD_STATUS_LOADING
            try:
                self._ensure_model()
                with self._preload_lock:
                    self._preload_jobs[job_id]["status"] = PRELOAD_STATUS_READY
            except Exception as exc:  # noqa: BLE001 - 任务状态机需要捕获一切失败
                with self._preload_lock:
                    self._preload_jobs[job_id]["status"] = PRELOAD_STATUS_FAILED
                    self._preload_jobs[job_id]["error"] = str(exc)

        threading.Thread(target=_run, daemon=True).start()
        return job_id

    def preload_status(self, job_id: str) -> dict[str, Any] | None:
        with self._preload_lock:
            job = self._preload_jobs.get(job_id)
            return dict(job) if job is not None else None

    def _ensure_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            model_dir = self._settings.whisper_model_dir
            kwargs: dict[str, object] = {
                "device": self._settings.whisper_device,
                "compute_type": "int8",
            }
            if model_dir is not None:
                kwargs["download_root"] = str(model_dir)
            self._model = WhisperModel(
                self._settings.whisper_model,
                **kwargs,  # type: ignore[arg-type]
            )
            return self._model

    def transcribe(self, wav_bytes: bytes) -> TranscriptionResult:
        """转写 WAV 音频字节。阻塞调用，由 API 层放入线程池执行。"""
        import tempfile

        model = self._ensure_model()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            segments, info = model.transcribe(
                tmp.name,
                language=None if self._settings.whisper_lang == "auto" else self._settings.whisper_lang,
                beam_size=1,
                vad_filter=True,
            )
            text = "".join(segment.text for segment in segments).strip()
        if info.language == "zh":
            # faster-whisper 的中文输出可能是繁体，统一转为简体
            text = simplify_chinese_text(text)
        return TranscriptionResult(
            text=text,
            language=info.language,
            duration=info.duration,
        )


class UnavailableSTT:
    """faster-whisper 未安装时的降级实现，返回可行动错误。"""

    def transcribe(self, wav_bytes: bytes) -> TranscriptionResult:
        raise RuntimeError(
            "语音识别组件不可用：请安装 faster-whisper 后重试（uv add faster-whisper）"
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "model": "unavailable",
            "device": "",
            "language": "auto",
            "model_cached": False,
            "model_dir": "",
        }

    def preload(self) -> str:
        raise RuntimeError(
            "语音识别组件不可用：请安装 faster-whisper 后重试（uv add faster-whisper）"
        )

    def preload_status(self, job_id: str) -> dict[str, Any] | None:
        return None
