import React from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand hover:bg-brand-dark text-black border-brand-dark font-extrabold",
  secondary: "bg-panel-head hover:bg-raised text-slate-100 border-line",
  danger: "bg-red-600 hover:bg-red-700 text-white border-red-500 font-extrabold",
  ghost: "bg-transparent hover:bg-raised text-slate-300 border-transparent",
};

interface Props extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  compact?: boolean;
}

export default function Button({
  variant = "secondary",
  compact = false,
  className = "",
  type = "button",
  ...rest
}: Props): React.ReactElement {
  // Full size meets the 48 px touch target; compact is for dense presenter controls.
  const size = compact ? "min-h-9 px-3 text-xs" : "min-h-12 px-4 text-sm";
  return (
    <button
      type={type}
      className={`tactile-btn inline-flex items-center justify-center gap-1.5 rounded border font-display font-bold uppercase tracking-wider disabled:cursor-not-allowed disabled:opacity-45 ${size} ${VARIANTS[variant]} ${className}`}
      {...rest}
    />
  );
}
