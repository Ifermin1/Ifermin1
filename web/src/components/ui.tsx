import type { ReactNode } from "react";

export function Card({ title, children, right, className = "" }: { title?: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "ok" | "warn" | "bad" | "muted" }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

export function Badge({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  return <span className={`badge ${ok === null ? "muted" : ok ? "ok" : "bad"}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
