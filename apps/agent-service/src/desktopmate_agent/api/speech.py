"""语音转写 API：接收 WAV 音频，返回识别文本。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.speech.stt import TranscriptionResult, WhisperSTT


class TranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    duration: float | None = None


MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20MB


def create_speech_router(stt: WhisperSTT) -> APIRouter:
    router = APIRouter()

    @router.post("/api/transcribe")
    async def transcribe(
        audio: UploadFile,
        _: None = Depends(require_auth),
    ) -> TranscribeResponse:
        content = await audio.read()
        if not content:
            raise HTTPException(
                status_code=400,
                detail={"code": "empty_audio", "message": "音频内容为空"},
            )
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=400,
                detail={"code": "audio_too_large", "message": "音频不能超过 20MB"},
            )
        try:
            result: TranscriptionResult = await asyncio.to_thread(stt.transcribe, content)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "stt_unavailable", "message": str(exc)},
            ) from exc
        if not result.text:
            raise HTTPException(
                status_code=422,
                detail={"code": "no_speech", "message": "未识别到语音，请重新录音"},
            )
        return TranscribeResponse(
            text=result.text,
            language=result.language,
            duration=result.duration,
        )

    @router.get("/api/speech/status")
    def speech_status(_: None = Depends(require_auth)) -> dict[str, object]:
        """语音能力状态：模型名/设备/语言/是否已缓存/缓存目录（S5）。"""
        return stt.status()

    @router.post("/api/speech/preload")
    def speech_preload(_: None = Depends(require_auth)) -> dict[str, str]:
        """触发 whisper 模型后台下载+加载，返回 job id（S5）。"""
        try:
            job_id = stt.preload()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "stt_unavailable", "message": str(exc)},
            ) from exc
        return {"job_id": job_id}

    @router.get("/api/speech/preload/{job_id}")
    def speech_preload_status(
        job_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        """预加载任务状态：queued | downloading | loading | ready | failed（S5）。"""
        job = stt.preload_status(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "job_not_found", "message": "预加载任务不存在"},
            )
        result: dict[str, object] = {"status": job.get("status", "failed")}
        if job.get("error"):
            result["error"] = job["error"]
        return result

    return router
