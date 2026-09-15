from loguru import logger

from tradepilot.core.events import TOPIC_AUDIT, EventBus
from tradepilot.domain.audit import AuditEvent
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore


class AuditService:
    def __init__(self, store: SQLiteStore, bus: EventBus) -> None:
        self.store = store
        self.bus = bus

    def log(self, event_type: str, message: str, source: str | None = None, target: str | None = None,
            details: dict | None = None) -> AuditEvent:
        event = AuditEvent(event_type=event_type, message=message, source_account=source,
                           target_account=target, details=details)
        logger.info(f"AUDIT | {event.event_type} | {source or '-'} -> {target or '-'} | {message}")
        try:
            event.id = self.store.save_audit(event)
        except Exception as exc:
            logger.error(f"No se pudo guardar auditoría: {exc}")
        self.bus.publish_nowait(TOPIC_AUDIT, event.model_dump(mode="json"))
        return event

    def recent(self, limit: int = 50, event_type: str | None = None) -> list[AuditEvent]:
        try:
            return self.store.get_recent_audits(limit, event_type)
        except Exception as exc:
            logger.error(f"No se pudo leer auditoría: {exc}")
            return []
