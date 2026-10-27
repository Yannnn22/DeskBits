"""S8 健康检查：GET /api/health readiness。

组件：db / settings / model_provider / speech。任一组件失败时 status=degraded，
前端据此展示明确的错误提示（而非静默失败）。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from desktopmate_agent import __version__
from desktopmate_agent.models.provider import resolve_model_config


def create_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health(request: Request) -> dict[str, object]:
        settings = request.app.state.settings
        settings_store = request.app.state.settings_store
        components: dict[str, object] = {}

        # db：能打开并能读一个计数即视为 ok
        try:
            connection = sqlite3.connect(settings.knowledge_db_path, timeout=2)
            try:
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()
            finally:
                connection.close()
            components["db"] = "ok"
        except Exception as exc:  # noqa: BLE001
            components["db"] = f"error: {exc}"

        # settings：能读 profile/models 即视为 ok
        try:
            settings_store.get_profile()
            settings_store.get_models()
            components["settings"] = "ok"
        except Exception as exc:  # noqa: BLE001
            components["settings"] = f"error: {exc}"

        # model_provider：fake 视为可用（本地降级）；API key 缺失标记 degraded
        try:
            resolved = resolve_model_config(settings, settings_store)
            if resolved.provider == "fake":
                components["model_provider"] = {
                    "status": "ok",
                    "provider": "fake",
                    "name": resolved.model,
                    "note": "本地降级模型，回答质量受限",
                }
            else:
                components["model_provider"] = {
                    "status": "ok" if resolved.api_key_source != "none" else "degraded",
                    "provider": resolved.provider,
                    "name": resolved.model,
                    "api_key_source": resolved.api_key_source,
                }
        except Exception as exc:  # noqa: BLE001
            components["model_provider"] = {"status": "error", "message": str(exc)}

        # speech：真实 STT 状态
        try:
            stt = request.app.state.stt
            status = stt.status()
            components["speech"] = {
                "status": "ok" if status.get("enabled") else "unavailable",
                "model": status.get("model"),
                "model_cached": status.get("model_cached", False),
                "device": status.get("device"),
            }
        except Exception as exc:  # noqa: BLE001
            components["speech"] = {"status": "error", "message": str(exc)}

        degraded = any(
            (isinstance(value, dict) and value.get("status") not in ("ok",))
            or (isinstance(value, str) and value != "ok")
            for value in components.values()
        )
        return {
            "status": "degraded" if degraded else "ok",
            "version": __version__,
            "components": components,
        }

    return router
