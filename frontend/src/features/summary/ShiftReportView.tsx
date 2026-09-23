/** Shift summary (PRD F10). Same view for the operator (live, edge) and the admin (synced, cloud). */
import React from "react";
import Badge from "@/shared/ui/Badge";
import Panel from "@/shared/ui/Panel";
import type { ShiftReport } from "@/shared/types/api";
import { duration, label, number, shortTime } from "@/shared/utils/format";

function Stat({ name, value, hint }: { name: string; value: string; hint?: string }): React.ReactElement {
  return (
    <div className="rounded-md border border-line bg-well p-3">
      <p className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">{name}</p>
      <p className="mt-1 font-mono text-2xl font-bold text-white">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

function CountList({ items, empty }: { items: { type: string; count: number }[]; empty: string }): React.ReactElement {
  if (items.length === 0) return <p className="text-sm text-slate-400">{empty}</p>;
  return (
    <ul className="space-y-1 text-sm">
      {items.map((item) => (
        <li key={item.type} className="flex justify-between gap-3 text-slate-200">
          <span>{label(item.type)}</span>
          <span className="font-mono">{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ShiftReportView({ report }: { report: ShiftReport }): React.ReactElement {
  const { time } = report;
  const missingTime =
    report.source === "cloud"
      ? "Working and idle time appear once the machine has ended the shift and synced."
      : "No sensor data for this shift yet.";

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat name="Tasks done" value={`${report.tasks_done} of ${report.tasks_total}`} />
        <Stat
          name="Working time"
          value={time ? duration(time.working_minutes) : "No data"}
          hint={time ? `${number(time.engine_hours, 1)} engine hours` : undefined}
        />
        <Stat
          name="Idle time"
          value={time ? duration(time.idle_minutes) : "No data"}
          hint={time ? `${number(time.idle_ratio * 100, 0)}% of engine time` : undefined}
        />
        <Stat
          name="Incidents"
          value={String(report.incidents_total)}
          hint={report.incidents_critical > 0 ? `${report.incidents_critical} critical` : undefined}
        />
      </div>
      {!time && <p className="text-sm text-slate-400">{missingTime}</p>}

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Tasks" icon="assignment">
          {report.tasks.length === 0 ? (
            <p className="text-sm text-slate-400">No tasks on this shift.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {report.tasks.map((t) => (
                <li key={t.task_id} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-slate-200">
                    {label(t.task_type)} · {number(t.completed_quantity, 1)} of {number(t.target_quantity, 0)} {label(t.quantity_unit)}
                  </span>
                  <span className="flex items-center gap-2">
                    {t.working_minutes !== null && <span className="font-mono text-xs text-slate-400">{duration(t.working_minutes)}</span>}
                    <Badge tone={t.status === "done" ? "ok" : "info"}>{label(t.status)}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Safety and coaching" icon="verified_user">
          <div className="space-y-3">
            <CountList items={report.incidents} empty="No incidents." />
            <div className="border-t border-line pt-3">
              <CountList items={report.behavior} empty="No coaching notes." />
            </div>
          </div>
        </Panel>
        <Panel title="Recommended training" icon="school">
          {report.training.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing recommended from this shift.</p>
          ) : (
            <ul className="list-disc space-y-1 pl-5 text-sm text-slate-200">
              {report.training.map((m) => (
                <li key={m.module_id}>{m.title}</li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <p className="font-mono text-xs text-slate-400">
        {report.operator_name} on {report.machine_model} · shift {shortTime(report.scheduled_start)} to {shortTime(report.scheduled_end)} ·{" "}
        {report.source === "edge" ? "live from the machine" : "as synced to the cloud"}
      </p>
    </div>
  );
}
