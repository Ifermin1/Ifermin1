// Avisos en este dispositivo: notificaciones del navegador para los eventos importantes que llegan por WebSocket.
// Funcionan mientras la consola esté abierta o en segundo plano (Android/Chrome, PC); en iPhone solo con la app abierta.
// Para avisos con la app cerrada está Telegram (Riesgo → Avisos al teléfono).
import type { AuditEvent } from "./api";

const KEY = "tpx.notify";
export const IMPORTANT = new Set(["DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING", "DRAWDOWN_LIMIT", "DRAWDOWN_WARNING",
  "KILL_SWITCH_ON", "FLATTEN", "SCHEDULED_FLATTEN", "NAKED_CLOSE", "FOLLOWER_REJECTED", "ACCOUNT_LOCKED", "DESYNC", "OVERCLOSE_FIX",
  "ADDON_SILENT", "ADDON_DOWN", "ADDON_RESTART", "GAP", "ERROR", "ENTRY_MISSED"]);
const TITLE: Record<string, string> = { DAILY_LOSS_LIMIT: "Límite de pérdida diaria", DAILY_LOSS_WARNING: "Cerca del límite de pérdida", DAILY_PROFIT_TARGET: "Objetivo de ganancia alcanzado",
  DAILY_PROFIT_WARNING: "Cerca del objetivo de ganancia", DRAWDOWN_LIMIT: "Límite de drawdown", DRAWDOWN_WARNING: "Drawdown al 80 %", KILL_SWITCH_ON: "Kill switch activado",
  FLATTEN: "Cierre de cuenta", SCHEDULED_FLATTEN: "Cierre programado", NAKED_CLOSE: "Stop rechazado: cuenta cerrada", FOLLOWER_REJECTED: "Orden rechazada",
  ACCOUNT_LOCKED: "Cuenta bloqueada por el prop firm", DESYNC: "Seguidora desincronizada", OVERCLOSE_FIX: "Sobrecierre corregido", ADDON_SILENT: "Sin heartbeat del addon",
  ADDON_DOWN: "Addon caído", ADDON_RESTART: "Addon reiniciado", GAP: "Mensajes perdidos", ERROR: "Error", ENTRY_MISSED: "Entrada no ejecutada" };

export const supported = () => typeof window !== "undefined" && "Notification" in window;
export const enabled = () => { try { return supported() && Notification.permission === "granted" && localStorage.getItem(KEY) === "1"; } catch { return false; } };
export async function enable(): Promise<boolean> {
  if (!supported()) return false;
  const p = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  const ok = p === "granted";
  try { localStorage.setItem(KEY, ok ? "1" : "0"); } catch { /* modo privado */ }
  if (ok) void show({ id: null, timestamp: new Date().toISOString(), event_type: "TEST", source_account: null, target_account: null, message: "Los avisos en este dispositivo están activados.", details: null }, true);
  return ok;
}
export function disable() { try { localStorage.setItem(KEY, "0"); } catch { /* modo privado */ } }

const recent = new Map<string, number>();
export async function show(a: AuditEvent, force = false): Promise<void> {
  if (!force && (!enabled() || !IMPORTANT.has(a.event_type))) return;
  const key = `${a.event_type}|${a.target_account ?? ""}|${a.message.slice(0, 60)}`;
  const now = Date.now();
  if (!force && now - (recent.get(key) ?? 0) < 60000) return;
  recent.set(key, now);
  const title = (TITLE[a.event_type] ?? a.event_type) + (a.target_account ? ` · ${a.target_account}` : "");
  const opts: NotificationOptions = { body: a.message, tag: key, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png" };
  try {
    const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
    if (reg) { await reg.showNotification(title, opts); return; }
  } catch { /* sin service worker (http): notificación directa */ }
  try { new Notification(title, opts); } catch { /* no soportado */ }
}
