"""用户画像聚合 API：从 memory_items 生成结构化画像，写入 profile_facts。

画像字段 = 用户 scope 下已确认（active）且置信度达标的记忆条目；
display_name 取自 settings profile。每条事实保留来源 memory id 与置信度。
"""

from fastapi import APIRouter, Depends

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.memory.models import MemoryScope
from desktopmate_agent.profile.store import ProfileFactsStore
from desktopmate_agent.settings.store import SettingsStore

MIN_CONFIDENCE = 0.5


def create_profile_router(
    agent_service: AgentService,
    facts_store: ProfileFactsStore,
    settings_store: SettingsStore,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/profile")
    def get_profile(_: None = Depends(require_auth)) -> dict[str, object]:
        profile = _aggregate_profile(agent_service, facts_store, settings_store)
        return {"profile": profile, "facts": facts_store.list_facts()}

    @router.post("/api/profile/refresh")
    def refresh_profile(_: None = Depends(require_auth)) -> dict[str, object]:
        facts_store.clear()
        profile = _aggregate_profile(agent_service, facts_store, settings_store)
        return {"profile": profile, "facts": facts_store.list_facts()}

    return router


def _aggregate_profile(
    agent_service: AgentService,
    facts_store: ProfileFactsStore,
    settings_store: SettingsStore,
) -> dict[str, object]:
    profile_settings = settings_store.get_profile()
    profile: dict[str, object] = {"display_name": profile_settings.display_name}

    if agent_service.memory_store is None:
        return profile

    entries = agent_service.memory_store.read_scope(
        MemoryScope.USER, agent_service.memory_user_id
    )
    for entry in entries:
        if entry.confidence < MIN_CONFIDENCE or not entry.value.strip():
            continue
        profile[entry.key] = entry.value
        facts_store.upsert_fact(
            field=entry.key,
            value=entry.value,
            confidence=entry.confidence,
            source_memory_id=entry.id,
        )
    return profile
