import { useState, type ReactNode } from "react";
import { useStore } from "../lib/store";
import { Badge } from "./ui";
import { Icon, type IconName } from "./Icons";
import { livePnl } from "./AccountPanel";
import { signedMoney } from "../lib/format";

export type Route = "dashboard" | "performance" | "analysis" | "accounts" | "risk" | "audit";

const NAV: { id: Route; label: string; icon: IconName }[] = [
  { id: "dashboard", label: "Inicio", icon: "home" },
  { id: "performance", label: "Calendario", icon: "calendar" },
  { id: "analysis", label: "Análisis", icon: "activity" },
  { id: "accounts", label: "Cuentas", icon: "users" },
  { id: "risk", label: "Riesgo", icon: "shield" },
  { id: "audit", label: "Auditoría", icon: "list" },
];

/** Resumen siempre visible: P&L del día de las cuentas en juego, cuántas copian y el kill switch. */
export function StatusStrip({ className = "" }: { className?: string }) {
  const { client, accounts, rules, risk, health, prices, setRisk } = useStore();
  const [busy, setBusy] = useState(false);
  const master = health?.bridge.master_account ?? null;
  const copying = accounts.filter((a) => a.enabled && a.account_id !== master && rules.some((r) => r.enabled && r.follower_account.toLowerCase() === a.account_id.toLowerCase()));
  const inPlay = accounts.filter((a) => a.enabled && (a.account_id === master || copying.includes(a)));
  let total = 0; let est = false;
  let fees = 0;
  for (const a of inPlay) { const p = livePnl(a, prices); total += p.value - (a.commissions_today || 0); fees += a.commissions_today || 0; est ||= p.live; }
  const open = inPlay.reduce((s, a) => s + a.open_positions.length, 0);
  async function toggleKill() {
    if (!client) return;
    const active = !risk?.kill_switch;
    if (active && !confirm("¿KILL SWITCH? Se bloquean todas las réplicas y se cierran a mercado las posiciones de todas las cuentas.")) return;
    setBusy(true);
    try { setRisk(await client.killSwitch(active, active ? "desde la barra de estado" : undefined, active, true)); }
    catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  }
  return (
    <div className={`strip ${className}`}>
      <div className="strip-item">
        <span className="stat-label">P&L hoy{fees ? " neto" : ""}</span>
        <b className={`num ${total > 0 ? "ok" : total < 0 ? "bad" : ""}`} title={fees ? `comisiones del día: ${fees.toFixed(2)} $` : undefined}>{est && "≈ "}{signedMoney(total)}</b>
      </div>
      <div className="strip-item"><span className="stat-label">Copiando</span><b>{copying.length}</b></div>
      <div className="strip-item"><span className="stat-label">Posiciones</span><b className={open ? "warn" : ""}>{open}</b></div>
      <button className={risk?.kill_switch ? "primary small-btn" : "danger-solid small-btn"} disabled={busy || !client} onClick={() => void toggleKill()}>
        {risk?.kill_switch ? "Reanudar" : "DETENER"}
      </button>
    </div>
  );
}

const BUILT = new Date(__BUILD__);
const fmtBuild = (d: Date) => d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
/** Acceso remoto: la URL pública (túnel) con un QR para escanear desde el teléfono, o cómo publicarla si no la hay. */
function RemoteAccess({ onClose }: { onClose: () => void }) {
  const { health, session } = useStore();
  const url = health?.public_url ?? null;
  const base = session?.baseUrl.replace(/\/$/, "") ?? "";
  const qr = (u: string) => `${base}/api/public-url/qr.svg?url=${encodeURIComponent(u)}&token=${encodeURIComponent(session?.token ?? "")}`;
  const [copied, setCopied] = useState(false);
  const copy = async (u: string) => { try { await navigator.clipboard.writeText(u); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { /* sin portapapeles */ } };
  return (
    <div className="modal-back" onClick={onClose} data-testid="remote-modal">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="card-head"><h2><span className="card-icon"><Icon name="share" size={15} /></span>Acceso remoto</h2><button className="chip-btn" onClick={onClose}>Cerrar</button></div>
        {url ? (
          <>
            <p className="small">La consola está <b>publicada</b>. Escanea el código con el teléfono, entra con el token e instálala como app (Android: Chrome → Instalar aplicación · iPhone: Safari → Compartir → Añadir a pantalla de inicio).</p>
            <div className="remote-grid">
              <img className="qr" src={qr(url)} alt="QR de la URL pública" />
              <div className="remote-url">
                <a href={url} target="_blank" rel="noreferrer" data-testid="remote-url">{url}</a>
                <button className="small-btn" onClick={() => void copy(url)}>{copied ? "Copiado" : "Copiar enlace"}</button>
                {health?.token_weak && <p className="error">El token es débil: cámbialo en engine\.env (scripts\publish.ps1 genera uno) antes de compartir esta dirección.</p>}
                <p className="muted small">Quien tenga la URL y el token controla la copia. No compartas el token; si se filtra, cámbialo en engine\.env y reinicia el engine.</p>
              </div>
            </div>
          </>
        ) : (
          <>
            <p className="small">La consola <b>no está publicada</b>: solo se llega desde esta red ({base}).</p>
            <div className="remote-grid">
              <img className="qr" src={qr(base)} alt="QR de la URL local" />
              <div className="remote-url">
                <b>En casa (misma Wi-Fi)</b><span className="muted small">escanea este código con el teléfono.</span>
                <b>Desde cualquier sitio</b><span className="muted small">en el PC del engine ejecuta <code>scripts\publish.ps1</code> (túnel de Cloudflare, HTTPS, sin abrir puertos). Con <code>-Hostname consola.tudominio.com -Install</code> la dirección es fija y arranca sola. La guía lo explica paso a paso.</span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Botón "Reanudar" del aviso global de kill switch (sin cerrar posiciones). */
function KillResume() {
  const { client, setRisk } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <button className="primary small-btn" disabled={busy || !client} data-testid="kill-resume"
            onClick={async () => { if (!client) return; setBusy(true); try { setRisk(await client.killSwitch(false)); } catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); } }}>
      Reanudar copia
    </button>
  );
}

/** El engine sirve una consola compilada después que la que tiene cargada el navegador (caché o app instalada). */
const staleBuild = (webBuild: string | null | undefined) => !!webBuild && new Date(webBuild).getTime() - BUILT.getTime() > 3 * 60000;
async function hardReload() {
  try { for (const r of await navigator.serviceWorker?.getRegistrations?.() ?? []) await r.unregister(); } catch { /* sin service worker */ }
  try { for (const k of await caches?.keys?.() ?? []) await caches.delete(k); } catch { /* sin caché */ }
  window.location.reload();
}

export function Shell({ route, onRoute, children }: { route: Route; onRoute: (r: Route) => void; children: ReactNode }) {
  const { health, wsStatus, risk, logout, session, density, setDensity, theme, setTheme } = useStore();
  const connected = health?.bridge.connected ?? null;
  const stale = staleBuild(health?.web_build);
  const [remote, setRemote] = useState(false);
  return (
    <div className="shell">
      <aside className="sidenav">
        <div className="brand"><span className="brand-dot" /><span className="brand-text">TradePilot X</span></div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
              <span className="nav-icon"><Icon name={n.icon} size={16} /></span>{n.label}
            </button>
          ))}
        </nav>
        <div className="sidenav-foot">
          <div className="chips">
            <Badge ok={connected}>{health ? (health.mode === "mock" ? "Simulador" : "NinjaTrader") : "…"}</Badge>
            <Badge ok={wsStatus === "open" ? true : wsStatus === "connecting" ? null : false}>en vivo</Badge>
          </div>
          <div className="muted small">{session?.baseUrl}</div>
          <div className="muted small" title={BUILT.toISOString()}>consola del {fmtBuild(BUILT)}</div>
          <button className="link" onClick={() => setRemote(true)} data-testid="remote-open">{health?.public_url ? "Acceso remoto · publicada" : "Acceso remoto"}</button>
          <button className="link" onClick={logout}>Salir</button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">{NAV.find((n) => n.id === route)?.label}</div>
          <StatusStrip className="strip-desktop" />
          <div className="topbar-status">
            {risk?.kill_switch && <span className="badge bad blink">KILL SWITCH</span>}
            <Badge ok={connected}>{health ? (health.mode === "mock" ? "Simulador" : "NinjaTrader") : "…"} {connected ? "conectado" : "sin datos"}</Badge>
            <Badge ok={wsStatus === "open" ? true : wsStatus === "connecting" ? null : false}>en vivo</Badge>
            <button className="ghost icon-btn" title={theme === "light" ? "Tema oscuro" : "Tema claro"} aria-pressed={theme === "light"} data-testid="theme-toggle"
                    onClick={() => setTheme(theme === "light" ? "dark" : "light")}><Icon name={theme === "light" ? "moon" : "sun"} size={16} /></button>
            <button className={`ghost icon-btn ${density === "compact" ? "active" : ""}`} title={density === "compact" ? "Vista amplia" : "Vista compacta"}
                    aria-pressed={density === "compact"} onClick={() => setDensity(density === "compact" ? "cozy" : "compact")}>{density === "compact" ? "▤" : "▥"}</button>
          </div>
        </header>
        <main className="content">
          {risk?.kill_switch && route !== "dashboard" && (
            <div className="banner bad kill-banner" data-testid="kill-banner">
              <span><b>COPIA DETENIDA (kill switch)</b>{risk.kill_switch_at ? ` desde ${new Date(risk.kill_switch_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}{risk.kill_switch_reason ? ` · ${risk.kill_switch_reason}` : ""}. Ninguna entrada se replica hasta reanudar.</span>
              <KillResume />
            </div>
          )}
          {remote && <RemoteAccess onClose={() => setRemote(false)} />}
          {health?.public_url && health.token_weak && (
            <div className="banner bad" data-testid="weak-token-banner"><b>Consola publicada con un token débil.</b> Cualquiera que adivine el token controla la copia: cámbialo en engine\.env (o ejecuta scripts\publish.ps1, que genera uno) y reinicia el engine.</div>
          )}
          {stale && (
            <div className="banner update" data-testid="stale-banner">
              <b>Hay una versión nueva de la consola</b> (compilada el {fmtBuild(new Date(health!.web_build!))}; esta es del {fmtBuild(BUILT)}).
              <button className="primary small-btn" onClick={() => void hardReload()}>Recargar ahora</button>
            </div>
          )}
          {children}
        </main>
      </div>

      <StatusStrip className="strip-mobile" />
      <nav className="tabbar">
        {NAV.map((n) => (
          <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
            <span className="nav-icon"><Icon name={n.icon} size={18} /></span><span>{n.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
