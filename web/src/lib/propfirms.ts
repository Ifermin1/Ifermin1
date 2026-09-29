/* Catálogo de prop firms y tipos de cuenta: qué drawdown aplica (dinámico intradía, EOD o estático), dónde se bloquea el
 * suelo, objetivo, pérdida diaria y contratos por tamaño. Sirve para rellenar los límites de Riesgo de una cuenta con un
 * par de clics. Los importes son los publicados por cada firma y cambian con el tiempo: revísalos en su web y ajústalos
 * en el editor antes de aplicar (todo es editable). */

export type DdType = "intraday" | "eod" | "static";
export type Lock = "none" | "start" | "start+100";
export type PlanSize = { size: number; drawdown: number; target: number; dailyLoss: number; contracts: number };
export type Plan = { id: string; name: string; dd: DdType; lock: Lock; sizes: PlanSize[]; note?: string };
export type Firm = { id: string; name: string; plans: Plan[] };

export const DD_LABEL: Record<DdType, string> = { intraday: "dinámico (intradía)", eod: "EOD (cierre del día)", static: "estático (suelo fijo)" };
export const DD_SHORT: Record<DdType, string> = { intraday: "DD dinámico", eod: "DD EOD", static: "DD estático" };
export const LOCK_LABEL: Record<Lock, string> = { none: "el suelo sube siempre", start: "el suelo se bloquea en el saldo inicial", "start+100": "el suelo se bloquea en saldo inicial + 100" };

const sz = (size: number, drawdown: number, target: number, contracts: number, dailyLoss = 0): PlanSize => ({ size, drawdown, target, dailyLoss, contracts });

export const FIRMS: Firm[] = [
  { id: "apex", name: "Apex Trader Funding", plans: [
    { id: "eval", name: "Evaluación (trailing)", dd: "intraday", lock: "none", note: "Trailing en tiempo real con el flotante. Sin pérdida diaria.",
      sizes: [sz(25000, 1500, 1500, 4), sz(50000, 2500, 3000, 10), sz(75000, 2750, 4250, 12), sz(100000, 3000, 6000, 14), sz(150000, 5000, 9000, 17), sz(250000, 6500, 15000, 27), sz(300000, 7500, 20000, 35)] },
    { id: "pa", name: "PA (financiada)", dd: "intraday", lock: "start+100", note: "Mismo trailing; el suelo se bloquea en saldo inicial + 100. Regla de consistencia del 30 % para retirar.",
      sizes: [sz(25000, 1500, 0, 4), sz(50000, 2500, 0, 10), sz(75000, 2750, 0, 12), sz(100000, 3000, 0, 14), sz(150000, 5000, 0, 17), sz(250000, 6500, 0, 27), sz(300000, 7500, 0, 35)] },
    { id: "static", name: "Static (evaluación)", dd: "static", lock: "none", note: "Suelo fijo: saldo inicial − 625. Máximo 2 contratos.",
      sizes: [sz(100000, 625, 2000, 2)] },
    { id: "pa_static", name: "PA Static (financiada)", dd: "static", lock: "none", note: "Suelo fijo en saldo inicial − 625.",
      sizes: [sz(100000, 625, 0, 2)] },
  ] },
  { id: "topstep", name: "Topstep", plans: [
    { id: "combine", name: "Trading Combine", dd: "eod", lock: "none", note: "Maximum Loss Limit (EOD) y Daily Loss Limit.",
      sizes: [sz(50000, 2000, 3000, 5, 1000), sz(100000, 3000, 6000, 10, 2000), sz(150000, 4500, 9000, 15, 3000)] },
    { id: "xfa", name: "Express Funded / Live", dd: "eod", lock: "start", note: "El MLL deja de subir al llegar al saldo inicial. Regla de consistencia del 50 % para retirar.",
      sizes: [sz(50000, 2000, 0, 5, 1000), sz(100000, 3000, 0, 10, 2000), sz(150000, 4500, 0, 15, 3000)] },
  ] },
  { id: "mff", name: "MyFundedFutures", plans: [
    { id: "starter", name: "Starter (EOD)", dd: "eod", lock: "none", note: "Drawdown al cierre del día. Revisa la pérdida diaria en tu plan.",
      sizes: [sz(50000, 2000, 3000, 3), sz(100000, 3500, 6000, 6), sz(150000, 5000, 9000, 9)] },
    { id: "expert", name: "Expert (intradía)", dd: "intraday", lock: "none", note: "Trailing intradía.",
      sizes: [sz(50000, 2000, 4000, 5), sz(100000, 3000, 8000, 10), sz(150000, 4500, 12000, 15)] },
  ] },
  { id: "tpt", name: "Take Profit Trader", plans: [
    { id: "test", name: "Test (EOD)", dd: "eod", lock: "none",
      sizes: [sz(25000, 1500, 1500, 3), sz(50000, 2000, 3000, 6), sz(75000, 2500, 4500, 9), sz(100000, 3000, 6000, 12), sz(150000, 4500, 9000, 15)] },
    { id: "pro", name: "PRO (financiada)", dd: "eod", lock: "none",
      sizes: [sz(25000, 1500, 0, 3), sz(50000, 2000, 0, 6), sz(75000, 2500, 0, 9), sz(100000, 3000, 0, 12), sz(150000, 4500, 0, 15)] },
  ] },
  { id: "tradeify", name: "Tradeify", plans: [
    { id: "growth", name: "Growth (EOD)", dd: "eod", lock: "none", sizes: [sz(50000, 2000, 3000, 5), sz(100000, 3000, 6000, 10), sz(150000, 4500, 9000, 15)] },
    { id: "advanced", name: "Advanced (intradía)", dd: "intraday", lock: "none", sizes: [sz(50000, 2000, 3000, 5), sz(100000, 3000, 6000, 10), sz(150000, 4500, 9000, 15)] },
  ] },
  { id: "bulenox", name: "Bulenox", plans: [
    { id: "opt1", name: "Opción 1 (EOD)", dd: "eod", lock: "none", sizes: [sz(25000, 1500, 1500, 1), sz(50000, 2500, 3000, 3), sz(100000, 3000, 6000, 7), sz(150000, 4500, 9000, 10), sz(250000, 5500, 15000, 15)] },
    { id: "opt2", name: "Opción 2 (trailing intradía)", dd: "intraday", lock: "none", sizes: [sz(25000, 1500, 1500, 1), sz(50000, 2500, 3000, 3), sz(100000, 3000, 6000, 7), sz(150000, 4500, 9000, 10), sz(250000, 5500, 15000, 15)] },
  ] },
  { id: "e2t", name: "Earn2Trade", plans: [
    { id: "gauntlet_mini", name: "Gauntlet Mini (EOD)", dd: "eod", lock: "none", note: "Drawdown EOD y pérdida diaria.",
      sizes: [sz(50000, 2000, 3000, 6, 1100), sz(100000, 3500, 6000, 12, 2200), sz(150000, 5000, 9000, 15, 3300), sz(200000, 6000, 12000, 15, 4400)] },
  ] },
  { id: "custom", name: "Otro / personalizado", plans: [
    { id: "custom", name: "Personalizado", dd: "intraday", lock: "none", note: "Rellena tú los importes.", sizes: [sz(50000, 2500, 3000, 5)] },
  ] },
];

export const firmOf = (id: string) => FIRMS.find((f) => f.id === id);
export const planOf = (firm: string, plan: string) => firmOf(firm)?.plans.find((p) => p.id === plan);
export const sizeLabel = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}K` : String(n));
/** Etiqueta corta para el panel: "Apex 50K · PA · DD dinámico". */
export function profileLabel(firm: string, plan: string, size: number): string | null {
  if (!firm) return null;
  const f = firmOf(firm), p = planOf(firm, plan);
  const name = f?.name.split(" ")[0] ?? firm;
  return `${name} ${size ? sizeLabel(size) : ""}${p ? ` · ${p.name.replace(/\s*\(.*\)$/, "")}` : plan ? ` · ${plan}` : ""}`.trim();
}
/** Suelo del drawdown que impone el plan: fijo (estático) o tope al que deja de subir (bloqueo). 0 = sin tope. */
export function floorCap(dd: DdType, lock: Lock, size: number, drawdown: number): number {
  if (dd === "static") return Math.max(0, size - drawdown);
  if (lock === "start") return size;
  if (lock === "start+100") return size + 100;
  return 0;
}
