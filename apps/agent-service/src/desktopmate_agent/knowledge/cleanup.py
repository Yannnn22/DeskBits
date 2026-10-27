"""Knowledge retention cleanup helpers."""

from datetime import UTC, datetime, timedelta

from desktopmate_agent.knowledge.store import DocumentStore


def cleanup_stale(
    store: DocumentStore,
    *,
    now: datetime | None = None,
    stale_days: int = 30,
    importance_threshold: float = 0.3,
) -> int:
    reference_time = now or datetime.now(UTC)
    cutoff = reference_time - timedelta(days=stale_days)
    return store.deactivate_stale_documents(
        cutoff=cutoff,
        importance_threshold=importance_threshold,
    )
