"use client";
import { useState } from "react";
import { Badge, ErrorNote, Section, Table } from "@/components/ui";
import { api, dt, usePoll } from "@/lib/api";

export default function Traders() {
  const { data, error, reload } = usePoll<any[]>("/traders", 30000);
  const [addr, setAddr] = useState(""); const [label, setLabel] = useState(""); const [source, setSource] = useState("hyperliquid");
  const [msg, setMsg] = useState<string | null>(null); const [busy, setBusy] = useState<number | null>(null);
  const act = async (fn: () => Promise<any>, id: number | null = null) => {
    setBusy(id); setMsg(null);
    try { await fn(); await reload(); } catch (e: any) { setMsg(e.message); } finally { setBusy(null); }
  };
  const n = (v: any, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  return (
    <>
      <ErrorNote error={error} />
      <Section title="Ranking de traders">
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Sólo fuentes con operaciones observables: direcciones públicas de Hyperliquid (on-chain, API oficial) y tus señales propias firmadas.
          El historial de una dirección <strong>no</strong> es necesariamente el historial completo de un inversor. Ningún trader se copia hasta que lo verificas y activas; máximo 3 estrategias.
        </p>
        <Table head={["Trader", "Fuente", "Puntuación", "Rentab. verif.", "Drawdown", "Sharpe", "Meses +", "Historial", "Ops", "Replicable", "Riesgo", "Estado", "Copia", ""]} empty={!data?.length}>
          {data?.map((t) => {
            const m = t.metrics || {};
            return (
              <tr key={t.id} className="align-top">
                <td className="td"><div className="font-medium">{t.label || "—"}</div><div className="font-mono text-xs text-stone-500" title={t.external_id}>{t.external_id.length > 16 ? `${t.external_id.slice(0, 8)}…${t.external_id.slice(-6)}` : t.external_id}</div></td>
                <td className="td text-xs">{t.source === "hyperliquid" ? "Hyperliquid (on-chain)" : "Señales propias"}</td>
                <td className="td tabular font-semibold">{t.score == null ? "—" : `${n(t.score)}/100`}</td>
                <td className="td tabular">{m.total_return_pct != null ? `${n(m.total_return_pct)} %` : "—"}</td>
                <td className="td tabular">{m.max_drawdown_pct != null ? `${n(m.max_drawdown_pct)} %` : "—"}</td>
                <td className="td tabular">{n(m.sharpe, 2)}</td>
                <td className="td tabular">{m.monthly_positive_pct != null ? `${n(m.monthly_positive_pct, 0)} %` : "—"}</td>
                <td className="td tabular">{m.history_days != null ? `${n(m.history_days, 0)} d` : "—"}</td>
                <td className="td tabular">{m.trades ?? "—"}</td>
                <td className="td tabular">{m.replicable_pct != null ? `${n(m.replicable_pct, 0)} %` : "—"}</td>
                <td className="td">{m.risk_level || "—"}</td>
                <td className="td">
                  {t.verified ? <Badge status="ok">Verificado</Badge> : <Badge status="aviso">No apto</Badge>}
                  {t.notes?.length > 0 && <ul className="mt-1 max-w-xs whitespace-normal text-xs text-stone-500">{t.notes.map((x: string) => <li key={x}>· {x}</li>)}</ul>}
                  {t.last_evaluated_at && <div className="mt-1 text-xs text-stone-400">Evaluado {dt(t.last_evaluated_at)}</div>}
                </td>
                <td className="td">
                  <label className="inline-flex cursor-pointer items-center gap-2">
                    <input type="checkbox" checked={t.enabled} disabled={busy === t.id || (!t.verified && !t.enabled)}
                      onChange={(e) => act(() => api(`/traders/${t.id}`, { method: "PATCH", body: JSON.stringify({ enabled: e.target.checked }) }), t.id)} />
                    <span className="text-xs">{t.enabled ? "Activa" : "Inactiva"}</span>
                  </label>
                </td>
                <td className="td space-x-1">
                  <button className="btn-ghost !px-2 !py-1 text-xs" disabled={busy === t.id} onClick={() => act(() => api(`/traders/${t.id}/evaluate`, { method: "POST" }), t.id)}>{busy === t.id ? "…" : "Evaluar"}</button>
                </td>
              </tr>
            );
          })}
        </Table>
        {msg && <p className="text-sm text-red-700 dark:text-red-400">{msg}</p>}
      </Section>
      <Section title="Añadir candidato">
        <div className="card grid gap-3 md:grid-cols-[160px_1fr_1fr_auto]">
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="hyperliquid">Hyperliquid</option><option value="manual">Señales propias</option>
          </select>
          <input className="input font-mono" placeholder={source === "hyperliquid" ? "Dirección 0x… (40 hex)" : "Identificador de tu estrategia"} value={addr} onChange={(e) => setAddr(e.target.value)} />
          <input className="input" placeholder="Etiqueta (opcional)" value={label} onChange={(e) => setLabel(e.target.value)} />
          <button className="btn-primary" disabled={!addr} onClick={() => act(async () => { await api("/traders", { method: "POST", body: JSON.stringify({ source, external_id: addr, label }) }); setAddr(""); setLabel(""); })}>Añadir</button>
        </div>
        <p className="text-xs text-stone-500">Tras añadirlo pulsa «Evaluar»: se descargan su PnL histórico y sus operaciones de la API oficial y se calcula la puntuación. Criterios mínimos: ≥ 90 días, ≥ 20 operaciones, drawdown ≤ 40 %, ≥ 30 % del volumen replicable en Coinbase spot, rentabilidad positiva y frecuencia compatible con las comisiones.</p>
      </Section>
    </>
  );
}
