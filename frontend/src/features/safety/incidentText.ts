/** Operator-facing incident text: state the hazard, then the action (rules.md section 4). */
import type { Incident } from "@/shared/types/api";

const MESSAGES: Record<string, string> = {
  proximity: "Person or object close to the machine. Stop and check the area.",
  seatbelt: "Seatbelt unfastened while the machine can move. Fasten it before moving.",
  operator_not_seated: "Seat empty with the engine running and brake released. Set the park brake.",
  tilt: "Machine tilt near its limit. Reduce the angle before continuing.",
  overloading: "Bucket load above rated capacity. Reduce the load.",
  working_condition: "Poor visibility or high heat. Slow down and follow site rules.",
};

export function incidentMessage(incident: Incident): string {
  if (incident.incident_type === "manual_report") return incident.description ?? "Manual report.";
  return MESSAGES[incident.incident_type] ?? "Safety check raised an alert. Stop and check.";
}

/** The operator still has something to do: acknowledge it, or wait for the hazard to clear. */
export function isActive(incident: Incident): boolean {
  return incident.status !== "resolved";
}

/** Highest-severity unresolved incident per type. */
export function activeByType(incidents: Incident[]): Map<string, Incident> {
  const map = new Map<string, Incident>();
  for (const incident of incidents.filter(isActive)) {
    const current = map.get(incident.incident_type);
    if (!current || (incident.peak_severity === "critical" && current.peak_severity !== "critical")) {
      map.set(incident.incident_type, incident);
    }
  }
  return map;
}
