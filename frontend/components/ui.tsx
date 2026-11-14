"use client";
import { ReactNode } from "react";

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "pos" | "neg" }) {
  return (
    <div className="card">
      <div className="text-xs font-medium text-stone-500 dark:text-stone-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular ${tone === "pos" ? "text-emerald-700 dark:text-emerald-400" : tone === "neg" ? "text-red-700 dark:text-red-400" : ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">{hint}</div>}
    </div>
  );
}

const STATUS: Record<string, { cls: string; icon: string }> = {
  ok: { cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300", icon: "✓" },
  aviso: { cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300", icon: "!" },
  bloqueado: { cls: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300", icon: "■" },
  info: { cls: "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300", icon: "·" },
};

export function Badge({ status, children }: { status: keyof typeof STATUS | string; children: ReactNode }) {
  const s = STATUS[status] || STATUS.info;
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}><span aria-hidden>{s.icon}</span>{children}</span>;
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">No se pudo contactar con el backend: {error}</div>;
}

export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="card overflow-x-auto p-0">
      <table className="min-w-full divide-y divide-stone-200 dark:divide-stone-800">
        <thead><tr>{head.map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-stone-100 dark:divide-stone-800">{children}</tbody>
      </table>
      {empty && <div className="p-6 text-center text-sm text-stone-500">Sin datos todavía.</div>}
    </div>
  );
}
