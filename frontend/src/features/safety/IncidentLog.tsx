import React from "react";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import Panel from "@/shared/ui/Panel";
import type { IncidentStatus } from "@/shared/types/api";
import { label, shortTime } from "@/shared/utils/format";
import { incidentMessage } from "./incidentText";

const STATUS_TONE: Record<IncidentStatus, "critical" | "info" | "ok"> = {
  open: "critical",
  acknowledged: "info",
  resolved: "ok",
};

/** Every incident of the current shift, newest first. */
export default function IncidentLog(): React.ReactElement {
  const { incidents } = useMachine();
  return (
    <Panel title="Shift incident log" icon="history" aside={<Badge>{incidents.length} total</Badge>}>
      {incidents.length === 0 ? (
        <p className="text-sm text-slate-400">No incidents this shift.</p>
      ) : (
        <ol className="divide-y divide-line">
          {incidents.map((incident) => (
            <li key={incident.incident_id} className="flex flex-wrap items-start justify-between gap-2 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-display text-sm font-bold uppercase text-slate-100">
                    {label(incident.incident_type)}
                  </span>
                  <Badge tone={incident.peak_severity === "critical" ? "critical" : "warning"}>
                    {incident.peak_severity}
                  </Badge>
                  <Badge tone={STATUS_TONE[incident.status]}>{incident.status}</Badge>
                </div>
                <p className="mt-1 text-xs text-slate-400">{incidentMessage(incident)}</p>
              </div>
              <span className="font-mono text-[11px] text-slate-400">
                {shortTime(incident.event_start)}
                {incident.event_end && incident.event_end !== incident.event_start
                  ? ` to ${shortTime(incident.event_end)}`
                  : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
