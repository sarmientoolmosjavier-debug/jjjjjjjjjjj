"use client";
import { Fragment, useState } from "react";
import { Badge, ErrorNote, Section, Table } from "@/components/ui";
import { REASONS, dt, eur, usePoll } from "@/lib/api";

const ST: Record<string, string> = { FILLED: "ok", OPEN: "info", PENDING: "info", CANCELLED: "aviso", REJECTED: "bloqueado", FAILED: "bloqueado", UNKNOWN: "aviso" };
const ST_ES: Record<string, string> = { FILLED: "Ejecutada", OPEN: "Abierta", PENDING: "Pendiente", CANCELLED: "Cancelada", REJECTED: "Rechazada", FAILED: "Fallida", UNKNOWN: "Por confirmar" };

export default function Operaciones() {
  const { data: orders, error } = usePoll<any[]>("/orders", 10000);
  const { data: decs } = usePoll<any[]>("/decisions", 10000);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <ErrorNote error={error} />
      <Section title="Operaciones" actions={<a className="btn-ghost" href="/api/export/orders.csv">Exportar CSV (para la declaración)</a>}>
        <Table head={["Fecha y hora", "Modo", "Trader", "Cripto", "Tipo", "Importe", "Precio", "Comisión", "Resultado", "Estado"]} empty={!orders?.length}>
          {orders?.map((o) => (
            <tr key={o.id}>
              <td className="td">{dt(o.created_at)}</td>
              <td className="td text-xs">{o.mode === "live" ? <Badge status="bloqueado">Real</Badge> : <Badge status="info">Simulada</Badge>}</td>
              <td className="td">{o.trader || "—"}</td>
              <td className="td">{o.product_id}</td>
              <td className="td">{o.side === "BUY" ? "Compra" : "Venta"} <span className="text-xs text-stone-500">({o.style})</span></td>
              <td className="td tabular">{eur(o.amount_eur)}</td>
              <td className="td tabular">{o.price ? eur(o.price) : "—"}</td>
              <td className="td tabular">{eur(o.fee_eur, 4)}</td>
              <td className={`td tabular ${o.pnl_eur && Number(o.pnl_eur) < 0 ? "text-red-700 dark:text-red-400" : ""}`}>{o.pnl_eur ? eur(o.pnl_eur) : "—"}</td>
              <td className="td"><Badge status={ST[o.status] || "info"}>{ST_ES[o.status] || o.status}</Badge>{o.error && <div className="text-xs text-stone-500">{o.error}</div>}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="Registro de decisiones">
        <p className="text-sm text-stone-600 dark:text-stone-400">Cada señal detectada queda registrada con su motivo, se ejecute o no.</p>
        <Table head={["Fecha", "Señal", "Activo", "Decisión", "Motivo", ""]} empty={!decs?.length}>
          {decs?.map((d) => (
            <Fragment key={d.id}>
              <tr>
                <td className="td">{dt(d.created_at)}</td>
                <td className="td">{d.side === "BUY" ? "Compra" : "Venta"} <span className="text-xs text-stone-500">{d.kind === "perp_long_open" || d.kind === "perp_long_close" ? "(derivada de perpetuo, replicada en spot sin apalancamiento)" : d.kind}</span></td>
                <td className="td">{d.asset}</td>
                <td className="td">{d.accepted ? <Badge status="ok">Aceptada</Badge> : <Badge status="aviso">Descartada</Badge>}</td>
                <td className="min-w-[280px] px-3 py-2 text-sm"><div className="font-medium">{REASONS[d.reason] || d.reason}</div><div className="text-xs text-stone-500">{d.message}</div></td>
                <td className="td"><button className="btn-ghost !px-2 !py-0.5 text-xs" onClick={() => setOpen(open === d.id ? null : d.id)}>{open === d.id ? "Ocultar" : "Detalle"}</button></td>
              </tr>
              {open === d.id && (
                <tr><td colSpan={6} className="bg-stone-50 p-3 dark:bg-stone-950">
                  {d.details?.costs && (
                    <div className="mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-6">
                      {[["Importe", eur(d.details.costs.amount_eur)], ["Com. entrada", `${d.details.costs.entry_fee_pct} %`], ["Com. salida", `${d.details.costs.exit_fee_pct} %`],
                        ["Spread", `${d.details.costs.spread_pct} %`], ["Deslizamiento", `${d.details.costs.slippage_pct} %`], ["Coste total", `${d.details.costs.total_cost_pct} %`],
                        ["Rentab. mínima", `${d.details.costs.breakeven_return_pct} %`], ["Umbral", `${d.details.costs.threshold_pct} %`], ["Fuente comisiones", d.details.costs.fee_source]].map(([k, v]) => (
                        <div key={k} className="card !p-2"><div className="text-stone-500">{k}</div><div className="tabular font-medium">{v}</div></div>))}
                    </div>
                  )}
                  <pre className="max-h-64 overflow-auto text-xs">{JSON.stringify(d.details, null, 2)}</pre>
                </td></tr>
              )}
            </Fragment>
          ))}
        </Table>
      </Section>
    </>
  );
}
