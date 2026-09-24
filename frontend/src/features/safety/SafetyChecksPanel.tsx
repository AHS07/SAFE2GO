/**
 * The seven safety checks. Each card shows the live sensor value and takes
 * its state from the backend safety engine (open incidents), never from a
 * threshold computed here. Without live data every card reads "No data",
 * because unknown is never shown as safe.
 */
import React from "react";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import Icon, { type IconName } from "@/shared/ui/Icon";
import Panel from "@/shared/ui/Panel";
import type { Incident, MachineStatus } from "@/shared/types/api";
import { number } from "@/shared/utils/format";
import ActiveAlerts from "./ActiveAlerts";
import { activeByType } from "./incidentText";

interface Check {
  name: string;
  icon: IconName;
  incidentType: string;
  value: (s: MachineStatus) => string;
}

const CHECKS: Check[] = [
  {
    name: "Seatbelt",
    icon: "airline_seat_recline_extra",
    incidentType: "seatbelt",
    value: (s) => (s.seatbelt_status === "fastened" ? "Fastened" : "Unfastened"),
  },
  {
    name: "Operator seat",
    icon: "person",
    incidentType: "operator_not_seated",
    value: (s) => (s.seat_occupied ? "Occupied" : "Empty"),
  },
  {
    name: "Proximity",
    icon: "radar",
    incidentType: "proximity",
    // No distance from a working sensor means nothing is in range; a sensor fault is unknown.
    value: (s) =>
      s.proximity_sensor_ok === false
        ? "Sensor fault"
        : s.proximity_distance === null
          ? "Nothing in range"
          : number(s.proximity_distance, 1, "m"),
  },
  {
    name: "Tilt",
    icon: "screen_rotation",
    incidentType: "tilt",
    value: (s) => number(s.tilt_angle, 1, "°"),
  },
  {
    name: "Bucket load",
    icon: "weight",
    incidentType: "overloading",
    value: (s) => number(s.payload_pct, 0, "%"),
  },
  {
    name: "Conditions",
    icon: "visibility",
    incidentType: "working_condition",
    value: (s) => `${number(s.visibility, 0, "m")} · ${number(s.ambient_temp, 1, "°C")}`,
  },
];

function CheckCard({ check, status, incident }: { check: Check; status: MachineStatus | null; incident?: Incident }) {
  const live = status?.live ?? false;
  const tone = incident ? (incident.peak_severity === "critical" ? "critical" : "warning") : live ? "ok" : "neutral";
  const border = {
    critical: "border-red-500 bg-red-950/40",
    warning: "border-amber-500 bg-amber-950/30",
    ok: "border-line bg-well",
    neutral: "border-dashed border-line bg-well",
  }[tone];
  const stateText = incident ? incident.peak_severity : live ? "OK" : "No data";

  return (
    <div className={`flex flex-col justify-between rounded-md border p-3 ${border}`}>
      <div className="flex items-start justify-between">
        <span className="font-display text-xs font-bold uppercase text-slate-300">{check.name}</span>
        <Icon name={check.icon} className="text-base text-brand" />
      </div>
      <div className="mt-2 font-mono text-base font-bold text-white">
        {live && status ? check.value(status) : "No data"}
      </div>
      <div className="mt-1">
        <Badge tone={tone}>{stateText}</Badge>
      </div>
    </div>
  );
}

export default function SafetyChecksPanel(): React.ReactElement {
  const { status, incidents, shift } = useMachine();
  const byType = activeByType(incidents);
  const openCount = incidents.filter((i) => i.status !== "resolved").length;

  return (
    <Panel
      title="Safety checks"
      icon="verified_user"
      aside={<Badge tone={openCount > 0 ? "critical" : "ok"}>{openCount > 0 ? `${openCount} open` : "All clear"}</Badge>}
      footer={
        <>
          <span>Alerts come from the on-machine safety engine</span>
          {shift && <span>Tilt limit {shift.tilt_limit_degrees}°</span>}
        </>
      }
    >
      <div className="space-y-4">
        <ActiveAlerts />
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {CHECKS.map((check) => (
            <CheckCard key={check.name} check={check} status={status} incident={byType.get(check.incidentType)} />
          ))}
        </div>
      </div>
    </Panel>
  );
}
