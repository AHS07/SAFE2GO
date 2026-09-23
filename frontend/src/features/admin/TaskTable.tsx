/** Tasks of one shift with cancel, reassign, and the ETA breakdown. */
import React, { useState } from "react";
import { adminApi } from "@/api/admin";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import type { AdminShift, AdminTask, AssignmentWarning, Delivery } from "@/shared/types/api";
import { useResource } from "@/shared/hooks/useResource";
import { duration, label, number, shortTime } from "@/shared/utils/format";
import { FormError, INPUT, Warnings } from "./forms";
import type { Reference } from "./useAdminData";
import { useToken } from "./useAdminData";

export function DeliveryBadge({ delivery }: { delivery: Delivery }): React.ReactElement {
  return delivery === "queued" ? (
    <Badge tone="warning">
      <Icon name="schedule_send" className="text-sm" /> Queued
    </Badge>
  ) : (
    <Badge tone="ok">
      <Icon name="done_all" className="text-sm" /> Delivered
    </Badge>
  );
}

function EtaBreakdownView({ taskId }: { taskId: string }): React.ReactElement {
  const token = useToken();
  const eta = useResource(() => adminApi.eta(token, taskId), [taskId]);
  if (eta.error) return <FormError error={eta.error} />;
  if (!eta.data) return <p className="text-sm text-slate-400">Loading estimate</p>;
  const b = eta.data;
  const rows: [string, string][] = [
    ["Historical average", duration(b.historical_average_minutes)],
    ["Model prediction", duration(b.raw_predicted_time)],
    ["Planning buffer", duration(b.buffer_minutes)],
    ["Planning ETA", duration(b.planning_eta)],
    ["Revised on the machine", b.revised_predicted_time === null ? "Not revised" : duration(b.revised_predicted_time)],
    ["Shown to the operator", duration(b.operator_eta)],
    ["Model version", `${b.model_version ?? "none"}${b.is_fallback ? " (historical average used)" : ""}`],
    ["Aggregates version", b.aggregates_version ?? "none"],
  ];
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([name, value]) => (
          <React.Fragment key={name}>
            <dt className="text-slate-400">{name}</dt>
            <dd className="font-mono text-slate-100">{value}</dd>
          </React.Fragment>
        ))}
      </dl>
      <div className="space-y-1 text-sm">
        <p className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">What is unusual about this task</p>
        {b.unusual_features.length === 0 ? (
          <p className="text-slate-400">Nothing stands out from typical tasks.</p>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-slate-200">
            {b.unusual_features.map((f) => (
              <li key={f.feature}>{f.description}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ReassignControl({
  task,
  shifts,
  reference,
  onDone,
}: {
  task: AdminTask;
  shifts: AdminShift[];
  reference: Reference;
  onDone: (warnings: AssignmentWarning[]) => void;
}): React.ReactElement {
  const token = useToken();
  const [target, setTarget] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const others = shifts.filter((s) => s.shift_id !== task.shift_id);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone((await adminApi.reassignTask(token, task.task_id, target)).warnings);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-64 flex-1 space-y-1">
          <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">Move to shift</span>
          <select className={INPUT} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose a shift</option>
            {others.map((s) => (
              <option key={s.shift_id} value={s.shift_id}>
                {reference.operatorName(s.operator_id)} on {reference.machineLabel(s.machine_id)}, {shortTime(s.scheduled_start)} to{" "}
                {shortTime(s.scheduled_end)}
              </option>
            ))}
          </select>
        </label>
        <Button variant="primary" disabled={!target || busy} onClick={() => void submit()}>
          Reassign
        </Button>
      </div>
      <FormError error={error} />
    </div>
  );
}

type Panel = { taskId: string; kind: "eta" | "reassign" } | null;

export default function TaskTable({
  tasks,
  shifts,
  reference,
  onChanged,
}: {
  tasks: AdminTask[];
  shifts: AdminShift[];
  reference: Reference;
  onChanged: (warnings?: AssignmentWarning[]) => void;
}): React.ReactElement {
  const token = useToken();
  const [panel, setPanel] = useState<Panel>(null);
  const [error, setError] = useState<unknown>(null);
  const [warnings, setWarnings] = useState<AssignmentWarning[]>([]);

  const toggle = (taskId: string, kind: "eta" | "reassign") =>
    setPanel((p) => (p?.taskId === taskId && p.kind === kind ? null : { taskId, kind }));

  const cancel = async (task: AdminTask) => {
    if (!window.confirm(`Cancel this ${label(task.task_type).toLowerCase()} task?`)) return;
    setError(null);
    try {
      await adminApi.cancelTask(token, task.task_id);
      onChanged();
    } catch (err) {
      setError(err);
    }
  };

  if (tasks.length === 0) return <p className="text-sm text-slate-400">No tasks on this shift yet.</p>;

  return (
    <div className="space-y-3">
      <FormError error={error} />
      <Warnings warnings={warnings} />
      <ul className="divide-y divide-line rounded-md border border-line">
        {tasks.map((task) => {
          const unstarted = task.status === "assigned";
          return (
            <li key={task.task_id} className="space-y-3 p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="space-y-1">
                  <p className="font-display text-base font-bold uppercase tracking-wide text-white">
                    {label(task.task_type)} · {number(task.target_quantity, 0)} {label(task.quantity_unit)} {label(task.material_type).toLowerCase()}
                  </p>
                  <p className="font-mono text-xs text-slate-400">
                    {shortTime(task.scheduled_start)} to {shortTime(task.scheduled_end)} · planning ETA {duration(task.planning_eta)} ·{" "}
                    {task.operator_skill_at_assignment}
                    {task.reassigned_at ? " · reassigned" : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={task.status === "cancelled" ? "neutral" : task.status === "done" ? "ok" : "info"}>{label(task.status)}</Badge>
                  <DeliveryBadge delivery={task.delivery} />
                  <Button compact onClick={() => toggle(task.task_id, "eta")}>
                    <Icon name="query_stats" className="text-sm" /> ETA
                  </Button>
                  {unstarted && (
                    <>
                      <Button compact onClick={() => toggle(task.task_id, "reassign")}>
                        <Icon name="swap_horiz" className="text-sm" /> Reassign
                      </Button>
                      <Button compact variant="danger" onClick={() => void cancel(task)}>
                        Cancel
                      </Button>
                    </>
                  )}
                </div>
              </div>
              {panel?.taskId === task.task_id && panel.kind === "eta" && <EtaBreakdownView taskId={task.task_id} />}
              {panel?.taskId === task.task_id && panel.kind === "reassign" && (
                <ReassignControl
                  task={task}
                  shifts={shifts}
                  reference={reference}
                  onDone={(w) => {
                    setPanel(null);
                    setWarnings(w);
                    onChanged(w);
                  }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
