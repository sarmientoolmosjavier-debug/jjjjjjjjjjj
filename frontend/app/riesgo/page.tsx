"use client";
import { Badge, ErrorNote, Section, Stat, Table } from "@/components/ui";
import { eur, pct, usePoll } from "@/lib/api";

export default function Riesgo() {
  const { data: r, error } = usePoll<any>("/risk", 10000);
  const { data: v } = usePoll<any>("/viability", 30000);
  return (
    <>
      <ErrorNote error={error} />
      <Section title="Control de riesgo">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Exposición total" value={pct(r?.exposure_pct, 1)} hint={eur(r?.invested)} />
          <Stat label="Pérdida/ganancia del día" value={eur(r?.daily_pnl)} tone={r && Number(r.daily_pnl) < 0 ? "neg" : undefined} />
          <Stat label="Drawdown desde máximo" value={eur(r?.drawdown)} hint={`Máximo: ${eur(r?.peak_equity)}`} />
          <Stat label="Liquidez disponible" value={eur(r?.cash)} />
          <Stat label="Patrimonio" value={eur(r?.equity)} />
        </div>
        <Table head={["Límite", "Valor", "Límite", "Estado"]}>
          {r?.limits?.map((l: any) => (
            <tr key={l.name}><td className="td">{l.name}</td><td className="td tabular">{l.value}</td><td className="td tabular">{l.limit}</td>
              <td className="td"><Badge status={l.status}>{l.status === "ok" ? "Correcto" : l.status === "aviso" ? "Cerca del límite" : "Alcanzado"}</Badge></td></tr>
          ))}
        </Table>
        <p className="text-xs text-stone-500">Los límites en euros se refieren al capital de 100 € y se reducen proporcionalmente si el patrimonio baja. Pérdida diaria ≥ 2 € bloquea compras hasta el día siguiente (hora de Madrid). Drawdown ≥ 10 € suspende la estrategia hasta que la revises en Configuración. Las ventas que siguen al trader no se bloquean.</p>
      </Section>
      <Section title="Viabilidad de costes (optimización para 100 €)">
        {v && (
          <>
            <p className="text-sm">
              Comisiones {v.fee_source === "coinbase" ? <Badge status="ok">reales de tu cuenta</Badge> : <Badge status="aviso">estimadas (conservadoras)</Badge>} maker {Number(v.maker_fee_pct).toFixed(2)} % · taker {Number(v.taker_fee_pct).toFixed(2)} %. Importe de ejemplo {eur(v.amount_eur)}. Umbral {v.threshold_pct} %. Estilo de entrada: <strong>{v.order_style}</strong>; la salida se supone siempre taker.
            </p>
            <Table head={["Producto", "Disponible", "Mín. orden", "Spread", "Coste maker→taker", "Coste taker→taker", "Rentab. mínima", "¿Viable?"]}>
              {v.rows.map((row: any) => {
                const sel = row[v.order_style];
                return (
                  <tr key={row.product_id}>
                    <td className="td">{row.product_id}</td>
                    <td className="td">{row.available ? <Badge status="ok">Sí</Badge> : <Badge status="bloqueado">No / sin sincronizar</Badge>}</td>
                    <td className="td tabular">{row.quote_min_size ? eur(row.quote_min_size) : "—"}</td>
                    <td className="td tabular">{sel.spread_pct} %{row.spread_assumed && <span className="text-xs text-amber-700"> (supuesto)</span>}</td>
                    <td className="td tabular">{row.maker.total_cost_pct} %</td>
                    <td className="td tabular">{row.taker.total_cost_pct} %</td>
                    <td className="td tabular">{sel.breakeven_return_pct} %</td>
                    <td className="td">{sel.viable && row.available ? <Badge status="ok">Sí</Badge> : <Badge status="bloqueado">No</Badge>}</td>
                  </tr>
                );
              })}
            </Table>
            {v.message && <p className="text-sm text-amber-800 dark:text-amber-300">{v.message}</p>}
          </>
        )}
      </Section>
    </>
  );
}
