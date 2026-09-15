import sqlite3
import json
from pathlib import Path
from datetime import datetime
from loguru import logger
from domain.replication import ReplicationRule
from domain.audit import AuditEvent

class SQLiteStore:
    def __init__(self, db_path: str = "data/tradepilot.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS replication_rules (
                    id TEXT PRIMARY KEY,
                    master_account TEXT,
                    follower_account TEXT,
                    multiplier REAL,
                    symbol_filter TEXT,
                    enabled BOOLEAN
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    source_account TEXT,
                    target_account TEXT,
                    message TEXT,
                    details TEXT
                )
            ''')
            conn.commit()

    # Rule operations
    def get_all_rules(self) -> list[ReplicationRule]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM replication_rules").fetchall()
            return [ReplicationRule(
                id=r['id'],
                master_account=r['master_account'],
                follower_account=r['follower_account'],
                multiplier=r['multiplier'],
                symbol_filter=r['symbol_filter'] if r['symbol_filter'] else None,
                enabled=bool(r['enabled'])
            ) for r in rows]

    def save_rule(self, rule: ReplicationRule):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO replication_rules 
                (id, master_account, follower_account, multiplier, symbol_filter, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (rule.id, rule.master_account, rule.follower_account, rule.multiplier, 
                  rule.symbol_filter, rule.enabled))

    def delete_rule(self, rule_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM replication_rules WHERE id = ?", (rule_id,))

    # Audit operations
    def save_audit(self, event: AuditEvent):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO audit_logs 
                (timestamp, event_type, source_account, target_account, message, details)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (event.timestamp.isoformat(), event.event_type, 
                  event.source_account, event.target_account, 
                  event.message, json.dumps(event.details) if event.details else None))

    def get_recent_audits(self, limit: int = 50) -> list[AuditEvent]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            return [AuditEvent(
                timestamp=datetime.fromisoformat(r['timestamp']),
                event_type=r['event_type'],
                source_account=r['source_account'],
                target_account=r['target_account'],
                message=r['message'],
                details=json.loads(r['details']) if r['details'] else None
            ) for r in rows]
