/** Assign work: shifts on the left, the selected shift's tasks on the right. */
import React, { useState } from "react";
import { adminApi } from "@/api/admin";
import ShiftReportView from "@/features/summary/ShiftReportView";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import type { AdminShift, AssignmentWarning } from "@/shared/types/api";
import { label, shortTime } from "@/shared/utils/format";
import { ShiftForm, TaskForm, Warnings } from "./forms";
import TaskTable, { DeliveryBadge } from "./TaskTable";
import type { Reference } from "./useAdminData";
import { usePolledResource, useReference, useToken } from "./useAdminData";

function shiftDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

function ShiftList({
  shifts,
  reference,
  selected,
  onSelect,
}: {
  shifts: AdminShift[];
  reference: Reference;
  selected: string | null;
  onSelect: (id: string) => void;
}): React.ReactElement {
  if (shifts.length === 0) return <p className="text-sm text-slate-400">No shifts yet. Create one below.</p>;
  return (
    <ul className="space-y-2" aria-label="Shifts">
      {shifts.map((s) => (
        <li key={s.shift_id}>
          <button
            type="button"
            onClick={() => onSelect(s.shift_id)}
            aria-pressed={selected === s.shift_id}
            className={`w-full min-h-12 rounded-md border px-3 py-2 text-left ${
              selected === s.shift_id ? "border-brand bg-brand/10" : "border-line bg-well hover:bg-raised"
            }`}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="font-display text-sm font-bold uppercase tracking-wide text-white">
                {reference.operatorName(s.operator_id)}
              </span>
              <DeliveryBadge delivery={s.delivery} />
            </span>
            <span className="mt-0.5 block font-mono text-xs text-slate-400">
              {reference.machineLabel(s.machine_id)} · {shiftDate(s.scheduled_start)} {shortTime(s.scheduled_start)} to{" "}
              {shortTime(s.scheduled_end)} · {label(s.weather_forecast)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ShiftSummaryPanel({ shiftId }: { shiftId: string }): React.ReactElement {
  const token = useToken();
  const [open, setOpen] = useState(false);
  return (
    <Panel
      title="Shift summary"
      icon="summarize"
      aside={
        <Button compact onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      {open ? <SummaryBody token={token} shiftId={shiftId} /> : <p className="text-sm text-slate-400">Tasks, time, safety, and training, as synced from the machine.</p>}
    </Panel>
  );
}

function SummaryBody({ token, shiftId }: { token: string; shiftId: string }): React.ReactElement {
  const report = usePolledResource(() => adminApi.shiftSummary(token, shiftId), [shiftId]);
  if (report.error) return <ErrorNotice error={report.error} onRetry={report.reload} />;
  if (!report.data) return <p className="text-sm text-slate-400">Loading</p>;
  return <ShiftReportView report={report.data} />;
}

function ShiftDetail({
  shift,
  shifts,
  reference,
}: {
  shift: AdminShift;
  shifts: AdminShift[];
  reference: Reference;
}): React.ReactElement {
  const token = useToken();
  const tasks = usePolledResource(() => adminApi.shiftTasks(token, shift.shift_id), [shift.shift_id]);
  const [warnings, setWarnings] = useState<AssignmentWarning[]>([]);

  return (
    <div className="space-y-4">
      <Panel
        title={`${reference.operatorName(shift.operator_id)} on ${reference.machineLabel(shift.machine_id)}`}
        icon="assignment"
        aside={<Badge tone="neutral">{shortTime(shift.scheduled_start)} to {shortTime(shift.scheduled_end)}</Badge>}
      >
        {tasks.error ? (
          <ErrorNotice error={tasks.error} onRetry={tasks.reload} />
        ) : tasks.data ? (
          <TaskTable tasks={tasks.data} shifts={shifts} reference={reference} onChanged={tasks.reload} />
        ) : (
          <p className="text-sm text-slate-400">Loading tasks</p>
        )}
      </Panel>
      <ShiftSummaryPanel shiftId={shift.shift_id} />
      <Panel title="Assign a task" icon="add_task">
        <div className="space-y-3">
          <Warnings warnings={warnings} />
          <TaskForm
            shiftId={shift.shift_id}
            onCreated={(w) => {
              setWarnings(w);
              tasks.reload();
            }}
          />
        </div>
      </Panel>
    </div>
  );
}

export default function AssignmentsPage(): React.ReactElement {
  const token = useToken();
  const reference = useReference();
  const shifts = usePolledResource(() => adminApi.shifts(token), [token]);
  const [selected, setSelected] = useState<string | null>(null);

  if (reference.error) return <ErrorNotice error={reference.error} onRetry={reference.reload} />;
  if (shifts.error) return <ErrorNotice error={shifts.error} onRetry={shifts.reload} />;
  if (!reference.data || !shifts.data) return <p className="text-sm text-slate-400">Loading</p>;

  const current = shifts.data.find((s) => s.shift_id === selected) ?? null;
  return (
    <div className="space-y-4">
      <PageHeader
        icon="assignment"
        title="Assignments"
        subtitle="Create shifts and tasks. Every assignment is checked before it is saved, then sent to the machine."
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_1fr]">
        <div className="space-y-4">
          <Panel title="Shifts" icon="event">
            <ShiftList shifts={shifts.data} reference={reference.data} selected={selected} onSelect={setSelected} />
          </Panel>
          <Panel title="New shift" icon="add">
            <ShiftForm
              reference={reference.data}
              onCreated={(shift) => {
                shifts.reload();
                setSelected(shift.shift_id);
              }}
            />
          </Panel>
        </div>
        {current ? (
          <ShiftDetail shift={current} shifts={shifts.data} reference={reference.data} />
        ) : (
          <Panel title="Tasks" icon="assignment">
            <p className="text-sm text-slate-400">Select a shift to see and assign its tasks.</p>
          </Panel>
        )}
      </div>
    </div>
  );
}
