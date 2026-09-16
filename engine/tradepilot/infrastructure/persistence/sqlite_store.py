import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from tradepilot.domain.audit import AuditEvent
from tradepilot.domain.replication import ReplicationRule
from tradepilot.domain.risk import RiskLimit


class SQLiteStore:
    """Persistencia local del engine. Una sola conexión protegida por lock:
    el volumen es bajo y así evitamos "database is locked"."""

    def __init__(self, db_path: str = "data/tradepilot.db") -> None:
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if str(db_path) != ":memory:":
            # Cada auditoría hacía un commit con fsync (~10 ms en Windows) en el camino crítico de la copia.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS replication_rules (
                    id TEXT PRIMARY KEY, master_account TEXT, follower_account TEXT,
                    multiplier REAL, symbol_filter TEXT, enabled BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, event_type TEXT,
                    source_account TEXT, target_account TEXT, message TEXT, details TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(timestamp DESC);
                CREATE TABLE IF NOT EXISTS risk_limits (
                    account_id TEXT PRIMARY KEY, max_daily_loss REAL, max_position_size INTEGER,
                    trading_halted BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY, enabled BOOLEAN DEFAULT 1, alias TEXT DEFAULT '',
                    first_seen TEXT, last_seen TEXT, last_balance REAL DEFAULT 0
                );
                """
            )
            # migraciones ligeras
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(accounts)").fetchall()}
            if "enabled_source" not in cols:
                self._conn.execute("ALTER TABLE accounts ADD COLUMN enabled_source TEXT DEFAULT 'auto'")
            if "last_connected" not in cols:
                self._conn.execute("ALTER TABLE accounts ADD COLUMN last_connected TEXT")
            rule_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(replication_rules)").fetchall()}
            for col, ddl in (("target_root", "TEXT"), ("entry_mode", "TEXT DEFAULT 'market'"), ("tolerance_ticks", "INTEGER DEFAULT 2"),
                             ("entry_timeout_s", "INTEGER DEFAULT 5"), ("entry_fallback", "TEXT DEFAULT 'market'")):
                if col not in rule_cols:
                    self._conn.execute(f"ALTER TABLE replication_rules ADD COLUMN {col} {ddl}")
            rcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(risk_limits)").fetchall()}
            if "halted_reason" not in rcols:
                self._conn.execute("ALTER TABLE risk_limits ADD COLUMN halted_reason TEXT DEFAULT ''")
                self._conn.execute("ALTER TABLE risk_limits ADD COLUMN halted_at TEXT")

    # ---- reglas ----
    def get_all_rules(self) -> list[ReplicationRule]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM replication_rules").fetchall()
        return [ReplicationRule(id=r["id"], master_account=r["master_account"],
                                follower_account=r["follower_account"], multiplier=r["multiplier"],
                                symbol_filter=r["symbol_filter"] or None, enabled=bool(r["enabled"]),
                                target_root=r["target_root"] or None, entry_mode=r["entry_mode"] or "market",
                                tolerance_ticks=r["tolerance_ticks"] if r["tolerance_ticks"] is not None else 2,
                                entry_timeout_s=r["entry_timeout_s"] if r["entry_timeout_s"] is not None else 5,
                                entry_fallback=r["entry_fallback"] or "market") for r in rows]

    def save_rule(self, rule: ReplicationRule) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO replication_rules (id, master_account, follower_account, multiplier, symbol_filter, enabled, "
                "target_root, entry_mode, tolerance_ticks, entry_timeout_s, entry_fallback) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rule.id, rule.master_account, rule.follower_account, rule.multiplier, rule.symbol_filter, rule.enabled,
                 rule.target_root, rule.entry_mode, rule.tolerance_ticks, rule.entry_timeout_s, rule.entry_fallback),
            )

    def delete_rule(self, rule_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM replication_rules WHERE id = ?", (rule_id,))

    # ---- auditoría ----
    def save_audit(self, event: AuditEvent) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO audit_logs (timestamp, event_type, source_account, target_account, message, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event.timestamp.isoformat(), event.event_type, event.source_account, event.target_account,
                 event.message, json.dumps(event.details) if event.details else None),
            )
            return int(cur.lastrowid)

    def get_recent_audits(self, limit: int = 50, event_type: str | None = None) -> list[AuditEvent]:
        sql = "SELECT * FROM audit_logs"
        params: tuple = ()
        if event_type:
            sql += " WHERE event_type = ?"
            params = (event_type,)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, params + (limit,)).fetchall()
        return [AuditEvent(id=r["id"], timestamp=datetime.fromisoformat(r["timestamp"]), event_type=r["event_type"],
                           source_account=r["source_account"], target_account=r["target_account"],
                           message=r["message"], details=json.loads(r["details"]) if r["details"] else None)
                for r in rows]

    # ---- riesgo ----
    def get_risk_limits(self) -> list[RiskLimit]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM risk_limits").fetchall()
        return [RiskLimit(account_id=r["account_id"], max_daily_loss=r["max_daily_loss"] or 0.0,
                          max_position_size=r["max_position_size"] or 0, trading_halted=bool(r["trading_halted"]),
                          halted_reason=r["halted_reason"] or "",
                          halted_at=datetime.fromisoformat(r["halted_at"]) if r["halted_at"] else None)
                for r in rows]

    def save_risk_limit(self, limit: RiskLimit) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO risk_limits (account_id, max_daily_loss, max_position_size, trading_halted, halted_reason, halted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (limit.account_id, limit.max_daily_loss, limit.max_position_size, limit.trading_halted,
                 limit.halted_reason, limit.halted_at.isoformat() if limit.halted_at else None))

    # ---- cuentas conocidas ----
    def get_accounts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
        return [dict(r) for r in rows]

    def upsert_account_seen(self, account_id: str, balance: float, when: str, enabled: bool,
                            connected: bool | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO accounts (account_id, enabled, enabled_source, alias, first_seen, last_seen, last_balance, last_connected) "
                "VALUES (?, ?, 'auto', '', ?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET last_seen = excluded.last_seen, last_balance = excluded.last_balance, "
                "last_connected = COALESCE(excluded.last_connected, accounts.last_connected), "
                "enabled = CASE WHEN accounts.enabled_source = 'user' THEN accounts.enabled ELSE excluded.enabled END",
                (account_id, enabled, when, when, balance, when if connected else None))

    def set_account_settings(self, account_id: str, enabled: bool | None = None, alias: str | None = None,
                             source: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO accounts (account_id, enabled, alias) VALUES (?, 1, '')", (account_id,))
            if enabled is not None:
                self._conn.execute("UPDATE accounts SET enabled = ? WHERE account_id = ?", (enabled, account_id))
            if source is not None:
                self._conn.execute("UPDATE accounts SET enabled_source = ? WHERE account_id = ?", (source, account_id))
            if alias is not None:
                self._conn.execute("UPDATE accounts SET alias = ? WHERE account_id = ?", (alias, account_id))

    def delete_account(self, account_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
