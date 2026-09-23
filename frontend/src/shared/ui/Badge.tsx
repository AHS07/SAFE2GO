import React from "react";

export type Tone = "neutral" | "brand" | "ok" | "warning" | "critical" | "info";

export const TONES: Record<Tone, string> = {
  neutral: "bg-raised text-slate-300 border-line",
  brand: "bg-brand/10 text-brand border-brand/40",
  ok: "bg-emerald-500/10 text-emerald-400 border-emerald-500/40",
  warning: "bg-amber-500/10 text-amber-300 border-amber-500/50",
  critical: "bg-red-500/15 text-red-300 border-red-500/60",
  info: "bg-sky-500/10 text-sky-300 border-sky-500/40",
};

export default function Badge({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] font-bold uppercase ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
