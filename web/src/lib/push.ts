// Web Push: avisos con la consola CERRADA, sin Telegram. El navegador se suscribe con la clave pública del engine y el
// engine manda las notificaciones por el servicio push del navegador (Android/Chrome y PC; en iPhone, instalada en la
// pantalla de inicio con iOS 16.4+). Necesita HTTPS (la URL pública) o localhost: en http://IP no hay service worker.
import type { Client } from "./api";

export const supported = () => typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

const b64ToBytes = (b64: string) => { const pad = "=".repeat((4 - (b64.length % 4)) % 4); const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(raw, (c) => c.charCodeAt(0)); };

export async function current(): Promise<PushSubscription | null> {
  if (!supported()) return null;
  try { const reg = await navigator.serviceWorker.getRegistration(); return reg ? await reg.pushManager.getSubscription() : null; } catch { return null; }
}

/** Suscribe este dispositivo. Devuelve null si fue bien o el motivo si no. */
export async function subscribe(client: Client): Promise<string | null> {
  if (!supported()) return "Este navegador no admite avisos push.";
  const st = await client.pushState();
  if (!st.available) return "El engine no tiene Web Push: ejecuta scripts\\update.ps1 en el PC y reinicia.";
  const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (perm !== "granted") return "El navegador no dio permiso para notificaciones (revisa los ajustes del sitio).";
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg) return "Sin service worker: abre la consola por HTTPS (la URL pública de Acceso remoto) o instálala en la pantalla de inicio.";
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    try { sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(st.public_key) as BufferSource }); }
    catch (ex) { return `No se pudo suscribir: ${ex instanceof Error ? ex.message : String(ex)}`; }
  }
  await client.pushSubscribe(sub.toJSON() as Record<string, unknown>, navigator.userAgent);
  return null;
}

export async function unsubscribe(client: Client): Promise<void> {
  const sub = await current();
  if (!sub) return;
  try { await client.pushUnsubscribe(sub.endpoint); } catch { /* el engine la quitará sola al fallar */ }
  try { await sub.unsubscribe(); } catch { /* ya no existía */ }
}
