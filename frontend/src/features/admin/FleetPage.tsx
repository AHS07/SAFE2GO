/** Operators with qualifications, and machines with status. */
import React from "react";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge, { type Tone } from "@/shared/ui/Badge";
import type { ServiceStatus } from "@/shared/types/api";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import { label, number } from "@/shared/utils/format";
import { useReference } from "./useAdminData";

const CELL = "border-b border-line px-3 py-2 text-left";

const SERVICE_TONE: Record<ServiceStatus, Tone> = { ok: "neutral", due_soon: "warning", overdue: "critical" };
const SERVICE_LABEL: Record<ServiceStatus, string> = { ok: "OK", due_soon: "Due soon", overdue: "Overdue" };

export default function FleetPage(): React.ReactElement {
  const reference = useReference();
  if (reference.error) return <ErrorNotice error={reference.error} onRetry={reference.reload} />;
  if (!reference.data) return <p className="text-sm text-slate-400">Loading</p>;
  const { operators, machines } = reference.data;

  return (
    <div className="space-y-4">
      <PageHeader icon="groups" title="Operators and machines" subtitle="Qualifications and machine status used by assignment checks, and service suggestions from engine hours." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={`Operators (${operators.length})`} icon="badge">
          <table className="w-full text-sm">
            <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className={CELL}>Name</th>
                <th className={CELL}>Qualifications</th>
              </tr>
            </thead>
            <tbody>
              {operators.map((o) => (
                <tr key={o.operator_id}>
                  <td className={CELL}>{o.operator_name}</td>
                  <td className={CELL}>
                    <div className="flex flex-wrap gap-1">
                      {o.qualifications.map((q) => (
                        <Badge key={q.machine_type} tone="info">
                          {label(q.machine_type)} {q.skill_level}
                        </Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title={`Machines (${machines.length})`} icon="construction">
          <table className="w-full text-sm">
            <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className={CELL}>Model</th>
                <th className={CELL}>Type</th>
                <th className={CELL}>Bucket</th>
                <th className={CELL}>Age</th>
                <th className={CELL}>Status</th>
                <th className={CELL}>Service</th>
              </tr>
            </thead>
            <tbody>
              {machines.map((m) => (
                <tr key={m.machine_id}>
                  <td className={CELL}>{m.machine_model}</td>
                  <td className={CELL}>{label(m.machine_type)}</td>
                  <td className={CELL}>{number(m.bucket_capacity, 1, "m³")}</td>
                  <td className={CELL}>{m.machine_age} y</td>
                  <td className={CELL}>
                    <Badge tone={m.machine_status === "available" ? "ok" : "warning"}>{label(m.machine_status)}</Badge>
                  </td>
                  <td className={CELL} title={m.service.message ?? undefined}>
                    <Badge tone={SERVICE_TONE[m.service.status]}>{SERVICE_LABEL[m.service.status]}</Badge>
                    <span className="ml-2 font-mono text-xs text-slate-400">
                      {m.service.hours_since_service.toFixed(0)} / {m.service.service_interval_hours.toFixed(0)} h
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  );
}
