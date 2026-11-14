"use client";
import { useEffect, useState } from "react";
import { Badge, ErrorNote, Section } from "@/components/ui";
import { api, getToken, setToken, usePoll } from "@/lib/api";

const FIELDS: [string, string, string][] = [
  ["min_cash_reserve_eur", "Reserva mínima de liquidez (€)", "≥ 30"],
  ["max_total_exposure_eur", "Exposición máxima simultánea (€)", "≤ 70"],
  ["max_asset_exposure_eur", "Exposición máxima por cripto (€)", "≤ 20"],
  ["max_open_positions", "Máximo de posiciones abiertas", "≤ 4"],
  ["max_new_trades_per_day", "Máximo de operaciones nuevas/día", "≤ 3"],
  ["max_daily_loss_eur", "Pérdida diaria máxima (€)", "≤ 2"],
  ["max_drawdown_eur", "Límite de drawdown (€)", "≤ 10"],
  ["max_round_trip_cost_pct", "Coste máx. ida y vuelta (%)", "por defecto 1"],
  ["min_trade_eur", "Importe mínimo por operación (€)", "no se aumenta para alcanzarlo"],
  ["max_signal_age_seconds", "Antigüedad máxima de señal (s)", ""],
  ["max_spread_pct", "Spread máximo (%)", ""],
  ["min_trader_score", "Puntuación mínima del trader", "0–100"],
  ["allowed_assets", "Activos permitidos (coma)", "p. ej. BTC,ETH"],
];

export default function Configuracion() {
  const { data: st, error, reload } = usePoll<any>("/settings", 30000);
  const { data: cb, reload: reloadCb } = usePoll<any>("/coinbase/status", 60000);
  const { data: chk, reload: reloadChk } = usePoll<any>("/live/checklist", 60000);
  const [form, setForm] = useState<Record<string, any>>({});
  const [tok, setTok] = useState("");
  const [phrase, setPhrase] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setTok(getToken()); }, []);
  useEffect(() => { if (st) setForm(st.limits); }, [st]);
  const run = async (fn: () => Promise<any>, ok: string) => {
    setMsg(null);
    try { await fn(); setMsg({ ok: true, text: ok }); reload(); reloadChk(); reloadCb(); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const mode = st?.mode;
  return (
    <>
      <ErrorNote error={error} />
      {msg && <div className={`rounded-lg p-3 text-sm ${msg.ok ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" : "bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-200"}`}>{msg.text}</div>}

      <Section title="Parada de emergencia">
        <div className="card flex flex-wrap items-center gap-3">
          <button className="btn-danger !px-5 !py-3 text-base" onClick={() => confirm("¿Detener toda la operativa y cancelar las órdenes abiertas?") && run(() => api("/emergency-stop", { method: "POST" }), "Parada de emergencia activada. Órdenes abiertas canceladas; el modo real queda desactivado.")}>■ PARADA DE EMERGENCIA</button>
          <button className="btn-ghost" onClick={() => run(() => api("/emergency-stop/release", { method: "POST" }), "Parada liberada")}>Liberar parada</button>
          <button className="btn-ghost" onClick={() => run(() => api("/risk/review", { method: "POST" }), "Suspensión por drawdown revisada; el máximo se reinicia al patrimonio actual")}>He revisado el drawdown: reanudar</button>
          <p className="w-full text-xs text-stone-500">La parada no vende tus posiciones: no se liquida la cartera sin una regla previamente autorizada.</p>
        </div>
      </Section>

      <Section title="Modo de operación">
        <div className="card space-y-3">
          <div className="flex flex-wrap gap-2">
            <button className={mode === "observation" ? "btn-primary" : "btn-ghost"} onClick={() => run(() => api("/mode", { method: "POST", body: JSON.stringify({ mode: "observation" }) }), "Modo observación: se evalúan señales pero no se ejecuta nada")}>Observación</button>
            <button className={mode === "simulation" ? "btn-primary" : "btn-ghost"} onClick={() => run(() => api("/mode", { method: "POST", body: JSON.stringify({ mode: "simulation" }) }), "Modo simulación (100 € virtuales)")}>Simulación</button>
            <span className={`btn ${mode === "live" ? "bg-red-600 text-white" : "border border-dashed border-stone-300 text-stone-500 dark:border-stone-700"}`}>Real {mode === "live" ? "(activo)" : "(requiere comprobaciones)"}</span>
          </div>
          <p className="text-xs text-stone-500">El sistema nunca pasa a dinero real por sí solo ni por alcanzar una rentabilidad. Tras un reinicio vuelve a simulación salvo autorización persistente (desactivada por defecto).</p>
        </div>
      </Section>

      <Section title="Activación de dinero real">
        <div className="card space-y-3">
          <ul className="space-y-1 text-sm">
            {chk?.items?.map((i: any) => <li key={i.key} className="flex items-start gap-2">{i.ok ? <Badge status="ok">OK</Badge> : <Badge status="bloqueado">Falta</Badge>}<span>{i.message}</span></li>)}
          </ul>
          <div className="text-xs text-stone-500">Autorización: {chk?.authorization}</div>
          {chk?.all_ok ? (
            <div className="space-y-2">
              <p className="text-sm">Escribe exactamente: <code className="rounded bg-stone-100 px-1 dark:bg-stone-800">{st?.confirmation_phrase}</code></p>
              <input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} />
              <button className="btn-danger" disabled={phrase !== st?.confirmation_phrase} onClick={() => run(async () => { const r = await api("/live/activate", { method: "POST", body: JSON.stringify({ confirmation: phrase }) }); if (!r.activated) throw new Error(r.error || "No se pudo activar"); }, "Modo real activado")}>Activar operativa real</button>
            </div>
          ) : <p className="text-sm text-stone-600 dark:text-stone-400">El modo real no se puede activar hasta que todas las comprobaciones estén en verde.</p>}
        </div>
      </Section>

      <Section title="Conexión Coinbase">
        <div className="card space-y-2 text-sm">
          <div>Credenciales: {cb?.credentials ? <Badge status="ok">Cargadas desde el entorno</Badge> : <Badge status="aviso">No configuradas (sólo datos públicos)</Badge>}</div>
          {cb?.permissions && <div>Permisos: ver {String(cb.permissions.can_view)} · operar {String(cb.permissions.can_trade)} · transferir {cb.permissions.can_transfer ? <Badge status="bloqueado">sí (no permitido)</Badge> : <Badge status="ok">no</Badge>}</div>}
          {cb?.balances && <div>Saldos: {Object.entries(cb.balances).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</div>}
          {cb?.error && <div className="text-red-700">{cb.error}</div>}
          <div>Comisiones: maker {cb ? (Number(cb.fees.maker) * 100).toFixed(2) : "—"} % · taker {cb ? (Number(cb.fees.taker) * 100).toFixed(2) : "—"} % ({cb?.fees.source === "coinbase" ? "reales" : "estimación conservadora"})</div>
          <div>Productos sincronizados: {cb?.products_synced_at ? new Date(cb.products_synced_at).toLocaleString("es-ES") : "nunca (sin conexión)"}</div>
          {cb?.products?.map((p: any) => <div key={p.product_id} className="text-xs text-stone-500">{p.product_id}: mín. {p.quote_min_size} € · incremento base {p.base_increment} · {p.tradable ? "negociable" : "no negociable"}</div>)}
          <p className="text-xs text-stone-500">Las claves se configuran en <code>.env</code> / secretos de Docker, nunca desde el navegador. Crea la clave CDP como ECDSA y sólo con permisos «view» y «trade».</p>
        </div>
      </Section>

      <Section title="Límites de riesgo y estrategia">
        <div className="card space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            {FIELDS.map(([k, label, hint]) => (
              <label key={k} className="text-sm"><span className="mb-1 block font-medium">{label}</span>
                <input className="input tabular" value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                {hint && <span className="text-xs text-stone-500">{hint}</span>}
              </label>
            ))}
            <label className="text-sm"><span className="mb-1 block font-medium">Estilo de orden de entrada</span>
              <select className="input" value={form.order_style ?? "maker"} onChange={(e) => setForm({ ...form, order_style: e.target.value })}>
                <option value="maker">Maker (límite post-only, más barata, puede no ejecutarse)</option>
                <option value="taker">Taker (mercado, más cara)</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.hl_include_perp_longs} onChange={(e) => setForm({ ...form, hl_include_perp_longs: e.target.checked })} />
              Copiar largos en perpetuos como compras spot sin apalancamiento (señal derivada)</label>
          </div>
          <button className="btn-primary" onClick={() => run(() => api("/settings", { method: "PUT", body: JSON.stringify(form) }), "Límites guardados")}>Guardar límites</button>
          <p className="text-xs text-stone-500">Estrategias activas: se gestionan en la pestaña Traders (máximo 3). Los topes del perfil moderado no se pueden superar desde el panel.</p>
        </div>
      </Section>

      <Section title="Acceso y simulación">
        <div className="card grid gap-3 md:grid-cols-2">
          <label className="text-sm"><span className="mb-1 block font-medium">Token de administración (ADMIN_TOKEN)</span>
            <div className="flex gap-2"><input type="password" className="input" value={tok} onChange={(e) => setTok(e.target.value)} />
              <button className="btn-ghost" onClick={() => { setToken(tok); setMsg({ ok: true, text: "Token guardado en este navegador" }); }}>Guardar</button></div>
            <span className="text-xs text-stone-500">{st?.admin_token_required ? "Requerido para cambiar ajustes." : "El backend no exige token (define ADMIN_TOKEN)."}</span>
          </label>
          <div className="text-sm"><span className="mb-1 block font-medium">Reiniciar simulación</span>
            <button className="btn-ghost" onClick={() => confirm("¿Borrar el historial simulado y volver a 100 € virtuales?") && run(() => api("/simulation/reset", { method: "POST" }), "Simulación reiniciada con 100 € virtuales")}>Volver a 100 € virtuales</button>
          </div>
        </div>
      </Section>
    </>
  );
}
