from loguru import logger
from domain.audit import AuditEvent
from typing import List
from infrastructure.persistence.sqlite_store import SQLiteStore

class AuditService:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def log_event(self, event: AuditEvent):
        logger.info(f"AUDIT | {event.event_type} | {event.source_account or '-'} -> {event.target_account or '-'} | {event.message}")
        try:
            self.store.save_audit(event)
        except Exception as e:
            logger.error(f"Fallo al guardar log de auditoría en SQLite: {e}")

    def get_recent_logs(self, limit=50) -> List[AuditEvent]:
        try:
            return self.store.get_recent_audits(limit)
        except Exception as e:
            logger.error(f"Fallo al leer la auditoría de SQLite: {e}")
            return []
