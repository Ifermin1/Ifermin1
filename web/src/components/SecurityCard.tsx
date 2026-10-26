import { useState } from "react";
import { useStore } from "../lib/store";
import { Card } from "./ui";
import { hashPin, newSalt, validPin } from "../lib/lock";

/* Seguridad de la consola: PIN local (bloqueo por inactividad y antes de operar) y estado del token de solo lectura. */
export function SecurityCard() {
  const { lock, setLock, health } = useStore();
  const [pin1, setPin1] = useState("");
  const [pin2, setPin2] = useState("");
  const [cur, setCur] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const hasPin = !!lock.config.hash;
  const check = async (p: string) => hasPin && (await hashPin(p, lock.config.salt)) === lock.config.hash;
  const save = async () => {
    setMsg(null);
    if (hasPin && !(await check(cur))) { setMsg("El PIN actual no es correcto."); return; }
    if (!validPin(pin1)) { setMsg("El PIN debe tener entre 4 y 8 dígitos."); return; }
    if (pin1 !== pin2) { setMsg("Los dos PIN no coinciden."); return; }
    const salt = newSalt();
    setLock({ ...lock.config, salt, hash: await hashPin(pin1, salt) });
    setPin1(""); setPin2(""); setCur(""); setMsg("PIN guardado en este navegador.");
  };
  const remove = async () => {
    if (!(await check(cur))) { setMsg("El PIN actual no es correcto."); return; }
    setLock({ ...lock.config, hash: null }); setCur(""); setMsg("PIN quitado.");
  };
  return (
    <Card title="Seguridad de la consola" right={<span className={`badge ${hasPin ? "ok" : ""}`}>{hasPin ? "PIN activo" : "sin PIN"}</span>}>
      <div className="notify-grid">
        <div>
          <p className="muted small"><b>PIN local.</b> Bloquea esta consola tras unos minutos sin uso y, si lo marcas, lo pide antes de cualquier acción que mueva dinero (operar, cerrar, detener la copia). Solo vive en este navegador: ponlo en cada teléfono o PC. No sustituye al token.</p>
          <div className="notify-form">
            {hasPin && <label>PIN actual<input type="password" inputMode="numeric" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="off" data-testid="sec-cur" /></label>}
            <label>{hasPin ? "Nuevo PIN" : "PIN (4 a 8 dígitos)"}<input type="password" inputMode="numeric" value={pin1} onChange={(e) => setPin1(e.target.value)} autoComplete="off" data-testid="sec-pin1" /></label>
            <label>Repetir PIN<input type="password" inputMode="numeric" value={pin2} onChange={(e) => setPin2(e.target.value)} autoComplete="off" data-testid="sec-pin2" /></label>
          </div>
          <div className="chips">
            <button className="primary small-btn" onClick={() => void save()} data-testid="sec-save">{hasPin ? "Cambiar PIN" : "Activar PIN"}</button>
            {hasPin && <button className="small-btn" onClick={() => void remove()}>Quitar PIN</button>}
          </div>
          <div className="chips" style={{ marginTop: 10 }}>
            <label className="inline small">Bloquear tras
              <select value={lock.config.minutes} onChange={(e) => setLock({ ...lock.config, minutes: Number(e.target.value) })} data-testid="sec-minutes">
                {[0, 2, 5, 15, 30, 60].map((m) => <option key={m} value={m}>{m === 0 ? "nunca" : `${m} min`}</option>)}
              </select>
            </label>
            <label className="inline small"><input type="checkbox" checked={lock.config.requirePin} onChange={(e) => setLock({ ...lock.config, requirePin: e.target.checked })} data-testid="sec-require" /> pedir PIN antes de operar, cerrar o detener</label>
          </div>
          {msg && <p className="muted small" data-testid="sec-msg">{msg}</p>}
        </div>
        <div>
          <p className="muted small"><b>Token de solo lectura.</b> {health?.read_token_set
            ? "Está configurado (API_READ_TOKEN en engine\\.env). Quien entre con él ve todo pero el engine rechaza cualquier orden, cierre o cambio: úsalo para asistentes o para mirar desde el teléfono sin riesgo."
            : "No está configurado. Añade API_READ_TOKEN=<otro token largo> en engine\\.env y reinicia el engine: con ese token se puede ver todo sin poder operar ni cambiar nada."}</p>
          {health?.role === "read" && <p className="warn small">Esta sesión usa el token de solo lectura.</p>}
        </div>
      </div>
    </Card>
  );
}
