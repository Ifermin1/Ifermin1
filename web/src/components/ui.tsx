import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icons";

export function Card({ title, children, right, className = "", icon }: { title?: string; children: ReactNode; right?: ReactNode; className?: string; icon?: IconName }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-head">
          {title && <h2>{icon && <span className="card-icon"><Icon name={icon} size={15} /></span>}{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

/** Anillo de progreso (0-100) para un porcentaje: aciertos, días verdes, drawdown consumido… */
export function Ring({ pct, tone = "ok", size = 44, label }: { pct: number | null; tone?: "ok" | "bad" | "warn" | "muted" | "accent"; size?: number; label?: string }) {
  const r = (size - 6) / 2, c = 2 * Math.PI * r;
  const v = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <svg className={`ring ${tone}`} width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label ?? (pct === null ? "sin datos" : `${Math.round(v)} %`)}>
      <circle cx={size / 2} cy={size / 2} r={r} className="ring-track" />
      <circle cx={size / 2} cy={size / 2} r={r} className="ring-value" strokeDasharray={`${(c * v) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  );
}

/** Indicador: etiqueta, valor grande, línea secundaria y, opcionalmente, icono, anillo o barra ganado/perdido. */
export function Kpi({ label, value, sub, tone, icon, ring, bar, title, className = "" }: {
  label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: "ok" | "warn" | "bad" | "muted" | "accent"; icon?: IconName;
  ring?: number | null; bar?: [number, number]; title?: string; className?: string;
}) {
  const total = bar ? bar[0] + bar[1] : 0;
  return (
    <div className={`kpi ${tone ?? ""} ${className}`} title={title}>
      <div className="kpi-top">
        <span className="kpi-label">{label}</span>
        {icon && <span className="kpi-icon"><Icon name={icon} size={15} /></span>}
      </div>
      <div className="kpi-main">
        <b className="kpi-value">{value}</b>
        {ring !== undefined && <Ring pct={ring} tone={tone === "accent" || tone === "muted" ? "accent" : tone ?? "ok"} />}
      </div>
      {bar && total > 0 && <div className="kpi-bar"><i className="ok" style={{ width: `${(100 * bar[0]) / total}%` }} /><i className="bad" style={{ width: `${(100 * bar[1]) / total}%` }} /></div>}
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

/** Compatibilidad: el indicador antiguo de Inicio ahora es un Kpi sin icono. */
export function Stat({ label, value, tone, icon, sub }: { label: string; value: ReactNode; tone?: "ok" | "warn" | "bad" | "muted"; icon?: IconName; sub?: ReactNode }) {
  return <Kpi label={label} value={value} tone={tone} icon={icon} sub={sub} className="stat" />;
}

export function Badge({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  return <span className={`badge ${ok === null ? "muted" : ok ? "ok" : "bad"}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
