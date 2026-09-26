import { useMemo, useRef, useState } from "react";
import { Icon } from "./Icons";

/* Tarjeta de resultados para compartir (1200×630): P&L del mes, aciertos, factor de beneficio, días y barras diarias.
 * Se dibuja en SVG y se exporta a PNG con un canvas; no sale ningún dato de cuenta ni credencial. */
export type ShareData = { title: string; period: string; net: number; winRate: number | null; pf: number | null; green: number; red: number; trades: number;
                          accounts: number; bars: { day: string; net: number }[]; alias?: string };
const fmt = (n: number) => (n < 0 ? "−" : n > 0 ? "+" : "") + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

export function shareSvg(d: ShareData): string {
  const W = 1200, H = 630;
  const good = d.net >= 0;
  const bars = d.bars.slice(-31);
  const maxAbs = Math.max(1, ...bars.map((b) => Math.abs(b.net)));
  const bx = 700, by = 210, bw = 440, bh = 250, mid = by + bh / 2;
  const step = bars.length ? bw / bars.length : bw;
  const barSvg = bars.map((b, i) => {
    const h = (Math.abs(b.net) / maxAbs) * (bh / 2 - 8);
    const x = bx + i * step + step * 0.15, w = Math.max(2, step * 0.7);
    return `<rect x="${x.toFixed(1)}" y="${(b.net >= 0 ? mid - h : mid).toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(2, h).toFixed(1)}" rx="2" fill="${b.net >= 0 ? "#34d399" : "#f87171"}" opacity="0.9"/>`;
  }).join("");
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Inter, Segoe UI, Roboto, Helvetica, Arial, sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1a33"/><stop offset="0.55" stop-color="#0a1223"/><stop offset="1" stop-color="#070b14"/></linearGradient>
    <radialGradient id="glow" cx="0.15" cy="0" r="0.7"><stop offset="0" stop-color="${good ? "#34d399" : "#f87171"}" stop-opacity="0.35"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
    <radialGradient id="glow2" cx="0.95" cy="0.1" r="0.6"><stop offset="0" stop-color="#6366f1" stop-opacity="0.35"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
    <linearGradient id="brand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#6366f1"/></linearGradient>
  </defs>
  <rect width="${W}" height="${H}" rx="28" fill="url(#bg)"/><rect width="${W}" height="${H}" rx="28" fill="url(#glow)"/><rect width="${W}" height="${H}" rx="28" fill="url(#glow2)"/>
  <rect x="60" y="54" width="34" height="34" rx="9" fill="url(#brand)"/><circle cx="77" cy="71" r="7" fill="none" stroke="#fff" stroke-width="3" stroke-dasharray="30 14"/>
  <text x="108" y="79" fill="#fff" font-size="26" font-weight="800">TradePilot X</text>
  <text x="1140" y="79" fill="#8b9bb8" font-size="20" text-anchor="end">${esc(d.period)}${d.alias ? " · " + esc(d.alias) : ""}</text>
  <text x="60" y="160" fill="#8b9bb8" font-size="20" letter-spacing="2">${esc(d.title.toUpperCase())}</text>
  <text x="60" y="250" fill="${good ? "#34d399" : "#f87171"}" font-size="86" font-weight="800" letter-spacing="-3">${fmt(d.net)}</text>
  <g fill="#8b9bb8" font-size="17" letter-spacing="1.5">
    <text x="60" y="340">ACIERTOS</text><text x="230" y="340">FACTOR</text><text x="400" y="340">DÍAS</text><text x="60" y="440">OPERACIONES</text><text x="230" y="440">CUENTAS</text>
  </g>
  <g fill="#fff" font-size="34" font-weight="700">
    <text x="60" y="382">${d.winRate === null ? "—" : Math.round(d.winRate * 100) + "%"}</text>
    <text x="230" y="382">${d.pf === null ? "—" : d.pf === Infinity ? "∞" : d.pf.toFixed(2)}</text>
    <text x="400" y="382"><tspan fill="#34d399">${d.green}</tspan><tspan fill="#8b9bb8"> / </tspan><tspan fill="#f87171">${d.red}</tspan></text>
    <text x="60" y="482">${d.trades}</text><text x="230" y="482">${d.accounts}</text>
  </g>
  <rect x="${bx - 20}" y="${by - 20}" width="${bw + 40}" height="${bh + 40}" rx="18" fill="#ffffff" fill-opacity="0.04" stroke="#ffffff" stroke-opacity="0.08"/>
  <line x1="${bx}" y1="${mid}" x2="${bx + bw}" y2="${mid}" stroke="#8b9bb8" stroke-opacity="0.5" stroke-dasharray="4 4"/>
  ${barSvg}
  <text x="${bx}" y="${by - 32}" fill="#8b9bb8" font-size="15" letter-spacing="1.5">P&amp;L POR DÍA</text>
  <text x="60" y="586" fill="#8b9bb8" font-size="16">Copiador para NinjaTrader · resultados netos de comisiones según el bróker</text>
</svg>`;
}

async function toPng(svg: string): Promise<Blob> {
  const img = new Image();
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(new Error("no se pudo renderizar")); img.src = url; });
  const c = document.createElement("canvas"); c.width = 1200 * 2; c.height = 630 * 2;
  const ctx = c.getContext("2d")!; ctx.scale(2, 2); ctx.drawImage(img, 0, 0); URL.revokeObjectURL(url);
  return await new Promise<Blob>((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("sin imagen"))), "image/png"));
}

export function ShareModal({ data, onClose }: { data: ShareData; onClose: () => void }) {
  const svg = useMemo(() => shareSvg(data), [data]);
  const [msg, setMsg] = useState<string | null>(null);
  const link = useRef<HTMLAnchorElement>(null);
  const download = async () => {
    try {
      const blob = await toPng(svg);
      const a = link.current!; a.href = URL.createObjectURL(blob); a.download = `tradepilot-${data.period.replace(/\s+/g, "-").toLowerCase()}.png`; a.click();
      setMsg("PNG descargado.");
    } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); }
  };
  const copy = async () => {
    try {
      const blob = await toPng(svg);
      const Item = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem;
      if (!Item || !navigator.clipboard?.write) throw new Error("este navegador no permite copiar imágenes; usa Descargar");
      await navigator.clipboard.write([new Item({ "image/png": blob })]);
      setMsg("Imagen copiada: pégala en Telegram, WhatsApp o X.");
    } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); }
  };
  return (
    <div className="modal-bg" onClick={onClose} data-testid="share-modal">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="card-head"><h2><span className="card-icon"><Icon name="share" size={15} /></span>Compartir resultados</h2><button className="chip-btn" onClick={onClose}>Cerrar</button></div>
        <div className="share-preview" dangerouslySetInnerHTML={{ __html: svg }} />
        <p className="muted small">La tarjeta lleva solo cifras agregadas del periodo; ningún nombre de cuenta ni bróker.</p>
        <div className="modal-actions">
          <button className="small-btn" onClick={() => void copy()}><Icon name="copy" size={14} /> Copiar imagen</button>
          <button className="primary small-btn" onClick={() => void download()} data-testid="share-download"><Icon name="download" size={14} /> Descargar PNG</button>
          <a ref={link} style={{ display: "none" }}>descarga</a>
        </div>
        {msg && <p className="muted small">{msg}</p>}
      </div>
    </div>
  );
}
