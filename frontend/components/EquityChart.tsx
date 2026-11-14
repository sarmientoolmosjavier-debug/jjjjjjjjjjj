"use client";
import { useMemo, useRef, useState } from "react";
import { dt, eur } from "@/lib/api";

type Row = { ts: string; equity: string; btc_hold: string | null; cash_hold: string };
const SERIES = [
  { key: "equity", label: "Cartera", color: "var(--series-1)" },
  { key: "btc_hold", label: "Mantener BTC", color: "var(--series-2)" },
  { key: "cash_hold", label: "Mantener efectivo", color: "var(--series-3)" },
] as const;

export default function EquityChart({ rows }: { rows: Row[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const ref = useRef<SVGSVGElement>(null);
  const W = 800, H = 260, P = { l: 56, r: 120, t: 12, b: 28 };
  const pts = useMemo(() => rows.map((r) => ({ t: new Date(r.ts).getTime(), equity: +r.equity, btc_hold: r.btc_hold ? +r.btc_hold : null, cash_hold: +r.cash_hold })), [rows]);
  if (pts.length < 2) return <div className="card text-sm text-stone-500">La evolución aparecerá cuando el worker haya registrado al menos dos instantáneas (cada 5 minutos).</div>;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const vals = pts.flatMap((p) => [p.equity, p.cash_hold, ...(p.btc_hold != null ? [p.btc_hold] : [])]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.1, 0.5); lo -= pad; hi += pad;
  const x = (t: number) => P.l + ((t - t0) / Math.max(t1 - t0, 1)) * (W - P.l - P.r);
  const y = (v: number) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const path = (k: (typeof SERIES)[number]["key"]) => {
    let d = "", pen = false;
    for (const p of pts) { const v = p[k]; if (v == null) { pen = false; continue; } d += `${pen ? "L" : "M"}${x(p.t).toFixed(1)},${y(v).toFixed(1)}`; pen = true; }
    return d;
  };
  const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * (i + 0.5)) / 4);
  const last = pts[pts.length - 1];
  const hp = hover != null ? pts[hover] : null;
  // Etiquetas directas al final de cada serie, separadas al menos 14 px para que no se solapen
  const endLabels = SERIES.flatMap((s) => (last[s.key] == null ? [] : [{ key: s.key, label: s.label, y: y(last[s.key] as number) }])).sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) endLabels[i].y = Math.max(endLabels[i].y, endLabels[i - 1].y + 14);
  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0, bd = Infinity;
    pts.forEach((p, i) => { const d = Math.abs(x(p.t) - px); if (d < bd) { bd = d; best = i; } });
    setHover(best);
  };
  return (
    <div className="card">
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-stone-600 dark:text-stone-300">
        {SERIES.map((s) => <span key={s.key} className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />{s.label}</span>)}
        <button className="btn-ghost ml-auto !py-0.5 text-xs" onClick={() => setTable(!table)}>{table ? "Ver gráfico" : "Ver tabla"}</button>
      </div>
      {table ? (
        <div className="max-h-72 overflow-auto text-sm">
          <table className="min-w-full"><thead><tr><th className="th">Fecha</th>{SERIES.map((s) => <th key={s.key} className="th">{s.label}</th>)}</tr></thead>
            <tbody>{[...rows].reverse().map((r) => <tr key={r.ts}><td className="td">{dt(r.ts)}</td><td className="td tabular">{eur(r.equity)}</td><td className="td tabular">{eur(r.btc_hold)}</td><td className="td tabular">{eur(r.cash_hold)}</td></tr>)}</tbody></table>
        </div>
      ) : (
        <div className="relative">
          <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="h-auto w-full touch-none" role="img" aria-label="Evolución del patrimonio frente a mantener BTC y mantener efectivo"
            onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
            {ticks.map((v) => <g key={v}><line x1={P.l} x2={W - P.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth={1} />
              <text x={P.l - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill="var(--text-secondary)">{v.toFixed(1)} €</text></g>)}
            <text x={P.l} y={H - 8} fontSize={11} fill="var(--text-secondary)">{new Date(t0).toLocaleDateString("es-ES")}</text>
            <text x={W - P.r} y={H - 8} fontSize={11} textAnchor="end" fill="var(--text-secondary)">{new Date(t1).toLocaleDateString("es-ES")}</text>
            {SERIES.map((s) => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
            {endLabels.map((l) => <text key={l.key} x={W - P.r + 8} y={l.y} dominantBaseline="middle" fontSize={11} fill="var(--text-primary)">{l.label}</text>)}
            {hp && <g>
              <line x1={x(hp.t)} x2={x(hp.t)} y1={P.t} y2={H - P.b} stroke="var(--text-secondary)" strokeWidth={1} strokeDasharray="3 3" />
              {SERIES.map((s) => { const v = hp[s.key]; return v == null ? null : <circle key={s.key} cx={x(hp.t)} cy={y(v)} r={4} fill={s.color} stroke="var(--surface-1)" strokeWidth={2} />; })}
            </g>}
          </svg>
          {hp && (
            <div className="pointer-events-none absolute top-2 rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow dark:border-stone-700 dark:bg-stone-900"
              style={{ left: `${Math.min((x(hp.t) / W) * 100, 70)}%` }}>
              <div className="mb-1 font-medium">{dt(new Date(hp.t).toISOString())}</div>
              {SERIES.map((s) => <div key={s.key} className="flex items-center gap-2"><span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />{s.label}<span className="ml-auto pl-3 tabular">{eur(hp[s.key])}</span></div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
