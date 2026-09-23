/** Display formatting. Values arrive from the backend; nothing here derives state. */

const LABELS: Record<string, string> = {
  m3: "m³",
  loads: "loads",
  operator_not_seated: "Operator not seated",
  working_condition: "Working conditions",
  manual_report: "Manual report",
  high_rpm_travel: "High-RPM travel",
};

/** snake_case value to a readable label: "material_loading" becomes "Material loading". */
export function label(value: string | null | undefined): string {
  if (!value) return "";
  if (LABELS[value]) return LABELS[value];
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "--:--";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "No estimate";
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return hours > 0 ? `${hours} h ${String(rest).padStart(2, "0")} min` : `${rest} min`;
}

export function number(value: number | null | undefined, digits = 0, unit = ""): string {
  if (value === null || value === undefined) return "No data";
  const text = value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit ? `${text} ${unit}` : text;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8).toUpperCase() : "";
}
