"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode } from "react";
import { usePoll } from "@/lib/api";

const NAV = [
  ["/", "Resumen"], ["/traders", "Traders"], ["/operaciones", "Operaciones"], ["/riesgo", "Riesgo"], ["/configuracion", "Configuración"],
];
const MODE: Record<string, { label: string; cls: string }> = {
  observation: { label: "Observación", cls: "bg-stone-200 text-stone-800 dark:bg-stone-800 dark:text-stone-200" },
  simulation: { label: "Simulación · 100 € virtuales", cls: "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-200" },
  live: { label: "DINERO REAL", cls: "bg-red-600 text-white" },
};

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { data } = usePoll<any>("/summary", 10000);
  const { data: health } = usePoll<any>("/health", 15000);
  const mode = MODE[data?.mode] || MODE.simulation;
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-white/90 backdrop-blur dark:border-stone-800 dark:bg-stone-950/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
          <div className="font-semibold">Smart Copy Trader</div>
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${mode.cls}`}>{mode.label}</span>
          {data?.emergency_stop && <span className="rounded-full bg-red-600 px-2.5 py-0.5 text-xs font-semibold text-white">■ Parada de emergencia</span>}
          {data?.drawdown_suspended && <span className="rounded-full bg-amber-500 px-2.5 py-0.5 text-xs font-semibold text-black">! Suspendida por drawdown</span>}
          {health && !health.worker_alive && <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-900">! Worker detenido</span>}
          <nav className="ml-auto flex flex-wrap gap-1">
            {NAV.map(([href, label]) => (
              <Link key={href} href={href} className={`rounded-lg px-3 py-1.5 text-sm ${path === href ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "hover:bg-stone-100 dark:hover:bg-stone-800"}`}>{label}</Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl space-y-8 px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-stone-500">
        Herramienta experimental. No es asesoramiento financiero ni garantiza rentabilidad. Los límites de pérdidas son mecanismos de control, no garantías de precio.
      </footer>
    </div>
  );
}
