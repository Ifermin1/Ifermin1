export const money = (n: number) => n.toLocaleString("es-ES", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
export const time = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString("es-ES", { hour12: false }) : "—");
export const ago = (iso: string | null | undefined) => {
  if (!iso) return "nunca";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `hace ${s}s` : s < 3600 ? `hace ${Math.round(s / 60)}m` : `hace ${Math.round(s / 3600)}h`;
};
export const signedMoney = (n: number) => (n > 0 ? "+" : "") + money(n);
export const pts = (n: number, decimals = 2) => (n > 0 ? "+" : "") + n.toFixed(decimals);
/** Raíz del contrato: "MNQ 12-26" -> "MNQ". */
export const root = (symbol: string) => symbol.split(" ")[0].toUpperCase();
/** Valor en dólares de un punto para los futuros habituales (para la estimación en vivo). */
const POINT_VALUE: Record<string, number> = { ES: 50, MES: 5, NQ: 20, MNQ: 2, YM: 5, MYM: 0.5, RTY: 50, M2K: 5, CL: 1000, MCL: 100, QM: 500,
  GC: 100, MGC: 10, SI: 5000, SIL: 1000, NG: 10000, QG: 2500, ZB: 1000, ZN: 1000, ZF: 1000, ZC: 50, ZS: 50, ZW: 50, "6E": 125000, "6J": 12500000, "6B": 62500, M6E: 12500, BTC: 5, MBT: 0.1 };
export const pointValue = (symbol: string): number | null => POINT_VALUE[root(symbol)] ?? null;
export const tickSize = (symbol: string): number => ({ ES: 0.25, MES: 0.25, NQ: 0.25, MNQ: 0.25, YM: 1, MYM: 1, RTY: 0.1, M2K: 0.1, CL: 0.01, MCL: 0.01, GC: 0.1, MGC: 0.1, ZB: 1 / 32, ZN: 1 / 64 } as Record<string, number>)[root(symbol)] ?? 0.25;
export const hhmm = (d: Date) => d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false });
