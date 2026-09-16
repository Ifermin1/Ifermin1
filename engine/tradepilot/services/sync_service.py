"""Vigila que cada seguidora vinculada tenga la posición que le corresponde (maestra x multiplicador).

Si difiere durante más de DESYNC_GRACE_SECONDS marca la cuenta como DESYNC: se bloquean las copias
que aumenten la exposición (las que la reducen siguen pasando) y la consola ofrece "igualar".
"""
import time
import uuid

from loguru import logger

from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.base import BrokerBridge
from tradepilot.services.account_service import AccountService
from tradepilot.services.audit_service import AuditService


class SyncService:
    def __init__(self, accounts: AccountService, bridge: BrokerBridge, audit: AuditService, bus: EventBus,
                 grace_seconds: float = 6.0) -> None:
        self.accounts = accounts
        self.bridge = bridge
        self.audit = audit
        self.bus = bus
        self.grace = grace_seconds
        self.rules_provider = lambda: []          # lo inyecta el contenedor (evita import circular)
        self._mismatch_since: dict[str, float] = {}

    # ---- cálculo ----
    def expected_positions(self, follower: str) -> dict[str, int] | None:
        """{raíz de símbolo: qty esperada} para una seguidora, o None si no está vinculada a la maestra actual."""
        master = self.bridge.health.master_account
        if not master:
            return None
        rules = [r for r in self.rules_provider() if r.enabled and r.master_matches(master)
                 and r.follower_account.lower() == follower.lower() and not r.symbol_filter]
        if not rules:
            return None
        mult = rules[0].multiplier
        msnap = self.accounts.accounts.get(master)
        expected: dict[str, int] = {}
        for p in (msnap.open_positions if msnap else []):
            root = p.symbol.split(" ")[0].upper()
            expected[root] = expected.get(root, 0) + int(round(p.quantity * mult))
        return expected

    def actual_positions(self, follower: str) -> dict[str, int]:
        snap = self.accounts.accounts.get(follower)
        actual: dict[str, int] = {}
        for p in (snap.open_positions if snap else []):
            root = p.symbol.split(" ")[0].upper()
            actual[root] = actual.get(root, 0) + p.quantity
        return actual

    def diff(self, follower: str) -> dict[str, tuple[int, int]]:
        """{raíz: (esperada, real)} solo donde difieren."""
        expected = self.expected_positions(follower)
        if expected is None:
            return {}
        actual = self.actual_positions(follower)
        return {k: (expected.get(k, 0), actual.get(k, 0)) for k in set(expected) | set(actual)
                if expected.get(k, 0) != actual.get(k, 0)}

    # ---- vigilancia (la llama AccountService tras cada sincronización) ----
    async def check(self) -> None:
        now = time.monotonic()
        changed = False
        for acc, snap in self.accounts.accounts.items():
            if not snap.enabled or acc == self.bridge.health.master_account:
                continue
            d = self.diff(acc)
            if not d:
                self._mismatch_since.pop(acc, None)
                if snap.desync:
                    snap.desync, snap.desync_detail, changed = False, "", True
                    self.audit.log("RESYNC", f"{acc} vuelve a coincidir con la maestra", target=acc)
                continue
            since = self._mismatch_since.setdefault(acc, now)
            if now - since >= self.grace and not snap.desync:
                detail = ", ".join(f"{k}: esperado {e:+d}, real {a:+d}" for k, (e, a) in sorted(d.items()))
                snap.desync, snap.desync_detail, changed = True, detail, True
                self.audit.log("DESYNC", f"{acc} no coincide con la maestra ({detail}). Copias que aumenten exposición bloqueadas.",
                               target=acc, details={"diff": d})
        if changed:
            await self.accounts.publish_accounts(force=True)

    # ---- igualar ----
    async def resync(self, follower: str) -> list[dict]:
        """Manda a mercado la diferencia para que la seguidora quede como la maestra x multiplicador."""
        d = self.diff(follower)
        sent = []
        for root, (expected, actual) in d.items():
            delta = expected - actual
            if delta == 0:
                continue
            symbol = self._symbol_for(root, follower) or root
            action = "BUY" if delta > 0 else "SELL"
            oid = "SYNC-" + uuid.uuid4().hex[:8]
            await self.bridge.send_order(target_account=follower, action=action, symbol=symbol, quantity=abs(delta),
                                         order_type="MARKET", master_order_id=oid, msg_type="EXECUTION")
            sent.append({"symbol": symbol, "action": action, "quantity": abs(delta)})
            self.audit.log("SYNC_ORDER", f"Igualando {follower}: {action} {abs(delta)} {symbol} (esperado {expected:+d}, real {actual:+d})",
                           target=follower, details={"order_id": oid})
        if not sent:
            self.audit.log("SYNC_ORDER", f"{follower} ya coincide con la maestra; nada que igualar", target=follower)
        return sent

    def _symbol_for(self, root: str, follower: str) -> str | None:
        for acc in (self.bridge.health.master_account, follower):
            snap = self.accounts.accounts.get(acc or "")
            for p in (snap.open_positions if snap else []):
                if p.symbol.split(" ")[0].upper() == root:
                    return p.symbol
        return None
