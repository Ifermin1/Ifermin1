// Candado de la consola: un PIN local (solo en este navegador) que bloquea la pantalla tras X minutos sin uso y, si se
// quiere, se pide antes de cualquier acción que mueva dinero (operar, cerrar, kill switch). No sustituye al token del
// engine: evita el toque accidental en el teléfono y que alguien use una consola que quedó abierta.
const KEY = "tpx.lock";
export type LockConfig = { hash: string | null; salt: string; minutes: number; requirePin: boolean };

const DEFAULT: LockConfig = { hash: null, salt: "", minutes: 15, requirePin: true };

export function loadLock(): LockConfig {
  try { const raw = localStorage.getItem(KEY); return raw ? { ...DEFAULT, ...JSON.parse(raw) } : { ...DEFAULT }; } catch { return { ...DEFAULT }; }
}
export function saveLock(c: LockConfig) { try { localStorage.setItem(KEY, JSON.stringify(c)); } catch { /* modo privado */ } }

const toHex = (b: ArrayBuffer) => Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");
// FNV-1a de 64 bits en JS para cuando no hay crypto.subtle (http://IP no es contexto seguro). Suficiente para un PIN local.
const fnv = (s: string) => { let h1 = 0x811c9dc5, h2 = 0x01000193; for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); h1 = Math.imul(h1 ^ c, 16777619) >>> 0; h2 = Math.imul(h2 ^ (c * 31), 16777619) >>> 0; } return h1.toString(16).padStart(8, "0") + h2.toString(16).padStart(8, "0"); };

export async function hashPin(pin: string, salt: string): Promise<string> {
  const data = `${salt}:${pin}`;
  try { if (crypto?.subtle) return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data))); } catch { /* sin subtle */ }
  return "fnv:" + fnv(data);
}
export const newSalt = () => { try { return toHex(crypto.getRandomValues(new Uint8Array(8)).buffer); } catch { return Math.random().toString(36).slice(2); } };
export const validPin = (pin: string) => /^\d{4,8}$/.test(pin);
