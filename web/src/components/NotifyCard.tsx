import { useEffect, useState } from "react";
import type { Client, NotifyState } from "../lib/api";
import { Card } from "./ui";
import * as local from "../lib/notify";

/* Avisos al teléfono: Telegram (con la app cerrada) y notificaciones del navegador (con la consola abierta). */
const GROUPS: { label: string; hint: string; types: string[] }[] = [
  { label: "Límites y protecciones", hint: "pérdida diaria, objetivo, drawdown, kill switch, cierres", types: ["DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING", "DRAWDOWN_LIMIT", "DRAWDOWN_WARNING", "KILL_SWITCH_ON", "KILL_SWITCH_OFF", "FLATTEN", "SCHEDULED_FLATTEN", "NAKED_CLOSE"] },
  { label: "Problemas de copia", hint: "rechazos, cuenta bloqueada, desincronización, entradas perdidas", types: ["FOLLOWER_REJECTED", "ACCOUNT_LOCKED", "DESYNC", "RESYNC", "OVERCLOSE_FIX", "ENTRY_MISSED", "BLOCKED", "TRIMMED"] },
  { label: "Addon y conexión", hint: "sin heartbeat, addon caído o reiniciado, mensajes perdidos, errores", types: ["ADDON_SILENT", "ADDON_DOWN", "ADDON_BACK", "ADDON_RESTART", "GAP", "ERROR"] },
  { label: "Cada operación", hint: "cada fill de una seguidora y cada operación del maestro (muchos mensajes)", types: ["FOLLOWER_FILL", "MASTER_RECEIVED"] },
];

export function NotifyCard({ client }: { client: Client }) {
  const [st, setSt] = useState<NotifyState | null>(null);
  const [form, setForm] = useState({ enabled: false, bot_token: "", chat_id: "", events: [] as string[] });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [localOn, setLocalOn] = useState(local.enabled());
  useEffect(() => { client.notifications().then((s) => { setSt(s); setForm({ enabled: s.enabled, bot_token: s.bot_token, chat_id: s.chat_id, events: s.events }); }).catch(() => {}); }, [client]);

  const groupState = (types: string[]) => { const on = types.filter((t) => form.events.includes(t)).length; return on === 0 ? "off" : on === types.length ? "on" : "some"; };
  const toggleGroup = (types: string[]) => {
    const all = groupState(types) === "on";
    setForm({ ...form, events: all ? form.events.filter((e) => !types.includes(e)) : Array.from(new Set([...form.events, ...types])) });
  };
  const run = async (what: string, fn: () => Promise<void>) => { setBusy(what); setMsg(null); try { await fn(); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); } };
  const save = () => run("save", async () => { const s = await client.setNotifications(form); setSt(s); setMsg(s.enabled ? "Avisos por Telegram guardados y activados." : "Guardado (avisos por Telegram desactivados)."); });
  const test = () => run("test", async () => { await client.setNotifications(form); const r = await client.testNotifications(); setMsg(r.ok ? "Mensaje de prueba enviado: mira Telegram." : `No se pudo enviar: ${r.error}`); setSt(await client.notifications()); });
  const discover = () => run("discover", async () => {
    const r = await client.discoverChat(form.bot_token);
    if (r.ok && r.chat_id) { setForm({ ...form, chat_id: r.chat_id }); setMsg(`Chat detectado: ${r.name || r.chat_id}. Guarda y envía una prueba.`); }
    else setMsg(r.error ?? "No se detectó el chat");
  });
  const toggleLocal = async () => {
    if (localOn) { local.disable(); setLocalOn(false); return; }
    const ok = await local.enable(); setLocalOn(ok);
    if (!ok) setMsg("El navegador no dio permiso para notificaciones (revisa los ajustes del sitio).");
  };

  return (
    <Card title="Avisos al teléfono" right={<label className="inline small"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} data-testid="notify-enabled" /> Telegram activado</label>}>
      <div className="notify-grid">
        <div>
          <p className="muted small"><b>Telegram</b> avisa aunque la consola esté cerrada. Tres pasos: 1) en Telegram, abre <b>@BotFather</b>, escribe <code>/newbot</code>, ponle nombre y copia el <i>token</i>; 2) abre el chat de tu bot nuevo y escríbele <code>/start</code>; 3) pega el token aquí, pulsa <i>Detectar chat</i>, guarda y envía una prueba.</p>
          <div className="notify-form">
            <label>Token del bot<input value={form.bot_token} onChange={(e) => setForm({ ...form, bot_token: e.target.value })} placeholder="123456789:AAH…" autoComplete="off" data-testid="notify-token" /></label>
            <label>Chat<span className="fee-row"><input value={form.chat_id} onChange={(e) => setForm({ ...form, chat_id: e.target.value })} placeholder="se detecta solo" data-testid="notify-chat" />
              <button type="button" className="ghost small-btn" disabled={busy !== null || !form.bot_token} onClick={() => void discover()} title="Busca el chat que escribió /start al bot">Detectar chat</button></span></label>
          </div>
          <div className="notify-groups">
            {GROUPS.map((g) => { const s = groupState(g.types); return (
              <label key={g.label} className="inline notify-group">
                <input type="checkbox" checked={s === "on"} ref={(el) => { if (el) el.indeterminate = s === "some"; }} onChange={() => toggleGroup(g.types)} />
                <span><b>{g.label}</b> <i className="muted">· {g.hint}</i></span>
              </label>
            ); })}
          </div>
          <div className="chips">
            <button className="primary small-btn" disabled={busy !== null} onClick={() => void save()} data-testid="notify-save">{busy === "save" ? "Guardando…" : "Guardar"}</button>
            <button className="small-btn" disabled={busy !== null || !form.bot_token || !form.chat_id} onClick={() => void test()}>{busy === "test" ? "Enviando…" : "Enviar prueba"}</button>
            {st && <span className="muted small">{st.stats.sent} enviados · {st.stats.errors} errores{st.stats.last_error ? ` · último: ${st.stats.last_error}` : ""}</span>}
          </div>
        </div>
        <div>
          <p className="muted small"><b>En este dispositivo</b>: notificaciones del navegador para los avisos importantes mientras la consola esté abierta o en segundo plano (Android y PC; en iPhone solo con la app abierta). No necesita configurar nada.</p>
          <button className={`small-btn ${localOn ? "primary" : ""}`} onClick={() => void toggleLocal()} disabled={!local.supported()} data-testid="notify-local">
            {!local.supported() ? "Este navegador no admite notificaciones" : localOn ? "Avisos en este dispositivo: activados" : "Activar avisos en este dispositivo"}
          </button>
        </div>
      </div>
      {msg && <p className="muted small" data-testid="notify-msg">{msg}</p>}
    </Card>
  );
}
