import React from "react";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import Panel from "@/shared/ui/Panel";
import { duration, label, number, shortTime } from "@/shared/utils/format";
import { STATUS_TONE } from "./taskState";

/** Every task in the shift, in planned order, with one planning estimate each. */
export default function TaskQueuePanel(): React.ReactElement {
  const { tasks, shift } = useMachine();
  const remaining = tasks.filter((t) => t.status !== "done").length;

  return (
    <Panel
      title="Shift task queue"
      icon="pending_actions"
      aside={<Badge>{remaining} open</Badge>}
      footer={
        shift ? (
          <>
            <span>
              Shift {shortTime(shift.scheduled_start)} to {shortTime(shift.scheduled_end)}
            </span>
            <span>Forecast: {label(shift.weather_forecast)}</span>
          </>
        ) : undefined
      }
    >
      {tasks.length === 0 ? (
        <p className="text-sm text-slate-400">No tasks assigned for this shift.</p>
      ) : (
        <ol className="space-y-2.5">
          {tasks.map((task, index) => (
            <li key={task.task_id} className="rounded-lg border border-line bg-well p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-display text-base font-bold uppercase text-white">
                  {index + 1}. {label(task.task_type)}
                </span>
                <Badge tone={STATUS_TONE[task.status]}>{label(task.status)}</Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 border-t border-line pt-2 font-mono text-xs text-slate-400">
                <span>
                  Material: <strong className="text-slate-200">{label(task.material_type)}</strong>
                </span>
                <span>
                  Target:{" "}
                  <strong className="text-slate-200">
                    {number(task.target_quantity, 0)} {label(task.quantity_unit)}
                  </strong>
                </span>
                <span>
                  Done:{" "}
                  <strong className="text-slate-200">
                    {number(task.completed_quantity, 1)} {label(task.quantity_unit)}
                  </strong>
                </span>
                <span>
                  Estimate: <strong className="text-emerald-400">{duration(task.eta_minutes)}</strong>
                </span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
