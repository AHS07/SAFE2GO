import React from "react";
import { TONES, type Tone } from "./Badge";
import Icon, { type IconName } from "./Icon";

const ICONS: Record<Tone, IconName> = {
  neutral: "info",
  brand: "info",
  ok: "check_circle",
  warning: "warning",
  critical: "error",
  info: "info",
};

export default function Notice({
  tone = "neutral",
  children,
  role,
}: {
  tone?: Tone;
  children: React.ReactNode;
  role?: "status" | "alert";
}): React.ReactElement {
  return (
    <div role={role} className={`flex items-start gap-2.5 rounded-md border px-3.5 py-3 text-sm ${TONES[tone]}`}>
      <Icon name={ICONS[tone]} className="mt-0.5 text-base" />
      <div className="flex-1 space-y-2 text-slate-100">{children}</div>
    </div>
  );
}
