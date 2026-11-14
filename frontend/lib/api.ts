"use client";
import { useCallback, useEffect, useState } from "react";

const TOKEN_KEY = "copytrader_admin_token";

export function getToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(t: string) {
  try { localStorage.setItem(TOKEN_KEY, t); } catch { /* sin almacenamiento */ }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as any) };
  const tok = getToken();
  if (tok) headers["X-Admin-Token"] = tok;
  const r = await fetch(`/api${path}`, { ...init, headers, cache: "no-store" });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* */ }
    throw new Error(msg);
  }
  return r.json();
}

export function usePoll<T>(path: string, ms = 10000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setData(await api<T>(path)); setError(null); } catch (e: any) { setError(e.message); }
  }, [path]);
  useEffect(() => { load(); const id = setInterval(load, ms); return () => clearInterval(id); }, [load, ms]);
  return { data, error, reload: load };
}

export const eur = (v: string | number | null | undefined, digits = 2) =>
  v === null || v === undefined || v === "" ? "—" :
  Number(v).toLocaleString("es-ES", { style: "currency", currency: "EUR", minimumFractionDigits: digits, maximumFractionDigits: digits });
export const pct = (v: string | number | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : `${Number(v).toLocaleString("es-ES", { minimumFractionDigits: digits, maximumFractionDigits: digits })} %`;
export const dt = (iso: string) => new Date(iso).toLocaleString("es-ES", { timeZone: "Europe/Madrid" });

export const REASONS: Record<string, string> = {
  executed: "Ejecutada", observation_mode: "Modo observación", cost_too_high: "Coste excesivo", stale_signal: "Señal antigua",
  stale_market_data: "Datos de mercado no recientes", trader_not_verified: "Trader no verificado", trader_disabled: "Copia desactivada",
  asset_not_allowed: "Activo no permitido", product_unavailable: "No disponible en Coinbase", new_listing: "Listado reciente",
  spread_too_wide: "Spread excesivo", below_minimum: "Por debajo del mínimo", no_sizing_info: "Sin tamaño relativo",
  daily_loss_limit: "Límite de pérdida diaria", drawdown_limit: "Límite de drawdown", strategy_suspended: "Estrategia suspendida",
  max_trades_per_day: "Máx. operaciones/día", max_open_positions: "Máx. posiciones", max_total_exposure: "Exposición total",
  max_asset_exposure: "Exposición por activo", cash_reserve: "Reserva de liquidez", duplicate: "Duplicada",
  emergency_stop: "Parada de emergencia", no_position: "Sin posición", unknown_trader: "Trader desconocido", rejected: "Rechazada", failed: "Fallida",
};
