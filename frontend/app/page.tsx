"use client";
import EquityChart from "@/components/EquityChart";
import { ErrorNote, Section, Stat, Table } from "@/components/ui";
import { eur, pct, usePoll } from "@/lib/api";

export default function Resumen() {
  const { data: s, error } = usePoll<any>("/summary", 10000);
  const { data: hist } = usePoll<any[]>("/history", 60000);
  const { data: via } = usePoll<any>("/viability", 60000);
  const pnl = s ? Number(s.pnl_eur) : 0;
  return (
    <>
      <ErrorNote error={error} />
      {via && !via.any_viable && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <strong>Operar no es económicamente viable ahora mismo.</strong> {via.message} Comisiones {via.fee_source === "coinbase" ? "reales de tu cuenta" : "estimadas (conservadoras, sin claves conectadas)"}: maker {Number(via.maker_fee_pct).toFixed(2)} % · taker {Number(via.taker_fee_pct).toFixed(2)} %.
        </div>
      )}
      <Section title="Resumen de cartera">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Capital inicial" value={eur(s?.initial_capital)} hint={s?.simulated ? "Virtual (simulación)" : "Capital gestionado real"} />
          <Stat label="Patrimonio" value={eur(s?.equity)} />
          <Stat label="Saldo disponible" value={eur(s?.cash)} hint={s && Number(s.reserved) > 0 ? `${eur(s.reserved)} reservado en órdenes` : undefined} />
          <Stat label="Capital invertido" value={eur(s?.invested)} />
          <Stat label="Rentabilidad" value={eur(s?.pnl_eur)} tone={pnl > 0 ? "pos" : pnl < 0 ? "neg" : undefined} hint={`Realizada: ${eur(s?.realized_pnl)}`} />
          <Stat label="Rentabilidad %" value={pct(s?.pnl_pct)} tone={pnl > 0 ? "pos" : pnl < 0 ? "neg" : undefined} />
          <Stat label="Comisiones acumuladas" value={eur(s?.fees_total, 4)} />
          <Stat label="Mantener BTC (comparación)" value={eur(s?.btc_hold)} hint={`Efectivo: ${eur(s?.cash_hold)}`} />
        </div>
      </Section>
      <Section title="Evolución histórica">
        <EquityChart rows={hist || []} />
      </Section>
      <Section title="Posiciones abiertas">
        <Table head={["Producto", "Trader", "Cantidad", "Coste", "Valor (bid)"]} empty={!s?.positions?.length}>
          {s?.positions?.map((p: any) => (
            <tr key={p.product_id + p.trader_id}><td className="td">{p.product_id}</td><td className="td">#{p.trader_id}</td>
              <td className="td tabular">{p.base_qty}</td><td className="td tabular">{eur(p.cost_eur)}</td><td className="td tabular">{eur(p.value_eur)}</td></tr>
          ))}
        </Table>
        {s?.missing_prices?.length > 0 && <p className="text-xs text-amber-700">Sin precio reciente para: {s.missing_prices.join(", ")} (valorado a coste).</p>}
      </Section>
    </>
  );
}
