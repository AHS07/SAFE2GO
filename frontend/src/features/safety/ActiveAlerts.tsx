/**
 * Open incidents with the acknowledge action. The backend decides whether
 * an acknowledgement is allowed (machine stationary, own incident) and
 * what it does: acknowledging an active CRITICAL keeps the alarm on.
 */
import React, { useState } from "react";
import { useMachine } from "@/features/machine/MachineContext";
import { errorMessage } from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import type { Incident } from "@/shared/types/api";
import { label, shortTime } from "@/shared/utils/format";
import { incidentMessage, isActive } from "./incidentText";

function AlertRow({ incident }: { incident: Incident }): React.ReactElement {
  const { acknowledge } = useMachine();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const critical = incident.peak_severity === "critical";
  const hazardActive = incident.event_end === null;

  const onAcknowledge = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await acknowledge(incident.incident_id);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li
      className={`rounded-md border p-3 ${
        critical ? "border-red-500/70 bg-red-950/40" : "border-amber-500/60 bg-amber-950/30"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon name={critical ? "error" : "warning"} className={critical ? "text-red-400" : "text-amber-300"} />
          <span className="font-display text-sm font-bold uppercase tracking-wide text-white">
            {label(incident.incident_type)}
          </span>
          <Badge tone={critical ? "critical" : "warning"}>{incident.peak_severity}</Badge>
          {incident.status === "acknowledged" && <Badge tone="info">Acknowledged</Badge>}
        </div>
        <span className="font-mono text-[11px] text-slate-400">{shortTime(incident.event_start)}</span>
      </div>
      <p className="mt-1.5 text-sm text-slate-100">{incidentMessage(incident)}</p>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-slate-400">
          {hazardActive ? "Hazard still present" : "Hazard cleared"}
          {incident.escalation_reason ? ` · escalated: ${label(incident.escalation_reason)}` : ""}
        </span>
        {incident.status === "open" && (
          <Button variant={critical ? "danger" : "secondary"} onClick={() => void onAcknowledge()} disabled={busy}>
            {busy ? "Sending" : "Acknowledge"}
          </Button>
        )}
      </div>
      {problem && (
        <p role="alert" className="mt-2 rounded border border-amber-500/50 bg-amber-950/60 px-2 py-1.5 font-mono text-xs text-amber-200">
          {problem}
        </p>
      )}
    </li>
  );
}

export default function ActiveAlerts({ emptyText = "No open incidents." }: { emptyText?: string }): React.ReactElement {
  const { incidents } = useMachine();
  const active = incidents.filter(isActive);
  if (active.length === 0) {
    return (
      <p className="flex items-center gap-2 rounded-md border border-dashed border-line p-3 text-sm text-slate-400">
        <Icon name="verified" className="text-emerald-400" />
        {emptyText}
      </p>
    );
  }
  return (
    <ul className="space-y-2" aria-label="Open incidents">
      {active.map((incident) => (
        <AlertRow key={incident.incident_id} incident={incident} />
      ))}
    </ul>
  );
}
