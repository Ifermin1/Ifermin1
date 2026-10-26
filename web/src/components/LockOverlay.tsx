import { useEffect, useState, type FormEvent } from "react";
import { useStore } from "../lib/store";

/* Pantalla de PIN: aparece cuando la consola se bloquea por inactividad o cuando una acción con dinero pide el PIN. */
export function LockOverlay() {
  const { lock, unlock, logout } = useStore();
  const [pin, setPin] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const visible = lock.locked || lock.asking;
  useEffect(() => { if (!visible) { setPin(""); setErr(null); } }, [visible]);
  if (!visible) return null;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await unlock(pin);
    if (!ok) { setErr("PIN incorrecto"); setPin(""); }
  };
  return (
    <div className="modal-bg lock-bg" data-testid="lock">
      <form className="modal lock-card" onSubmit={(e) => void submit(e)}>
        <div className="brand big"><span className="brand-dot" />TradePilot X</div>
        <p className="muted small">{lock.asking ? "Esta acción mueve dinero: introduce el PIN para continuar." : "Consola bloqueada por inactividad. Introduce el PIN."}</p>
        <input type="password" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} maxLength={8} autoFocus data-testid="lock-pin" placeholder="PIN" />
        {err && <p className="bad small">{err}</p>}
        <div className="modal-actions">
          {lock.asking && <button type="button" className="ghost" onClick={() => void unlock(null)} data-testid="lock-cancel">Cancelar</button>}
          {lock.locked && <button type="button" className="ghost" onClick={logout}>Salir</button>}
          <button className="primary" disabled={pin.length < 4} data-testid="lock-ok">Desbloquear</button>
        </div>
      </form>
    </div>
  );
}
