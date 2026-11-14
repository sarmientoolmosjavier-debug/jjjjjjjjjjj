import "./globals.css";
import type { Metadata } from "next";
import Shell from "@/components/Shell";

export const metadata: Metadata = { title: "Smart Copy Trader", description: "Copy trading verificable en Coinbase con control de costes y riesgo" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
