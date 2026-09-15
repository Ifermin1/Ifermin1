import { useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { ApiError } from "../lib/api";

export function Login() {
  const { login } = useStore();
  const [baseUrl, setBaseUrl] = useState(window.location.origin);
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try { await login({ baseUrl, token }); }
    catch (ex) { setErr(ex instanceof ApiError && ex.status === 401 ? "Token incorrecto" : `No se pudo conectar: ${ex instanceof Error ? ex.message : ex}`); }
    finally { setBusy(false); }
  }

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand big"><span className="brand-dot" />TradePilot X</div>
        <p className="muted">Conéctate al engine que corre junto a NinjaTrader.</p>
        <label>URL del engine<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://tradepilot.midominio.com" required /></label>
        <label>Token de acceso<input type="password" value={token} onChange={(e) => setToken(e.target.value)} required autoFocus /></label>
        {err && <p className="error">{err}</p>}
        <button className="primary" disabled={busy}>{busy ? "Conectando…" : "Entrar"}</button>
      </form>
    </div>
  );
}
