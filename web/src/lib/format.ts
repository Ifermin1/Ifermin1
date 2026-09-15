export const money = (n: number) => n.toLocaleString("es-ES", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
export const time = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString("es-ES", { hour12: false }) : "—");
export const ago = (iso: string | null | undefined) => {
  if (!iso) return "nunca";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `hace ${s}s` : s < 3600 ? `hace ${Math.round(s / 60)}m` : `hace ${Math.round(s / 3600)}h`;
};
