/**
 * The task the operator is working on, with only the actions its status
 * allows. Completion always needs confirmation, including when the
 * target quantity is reached (prd.md F1.5): tasks never close themselves.
 */
import React, { useState } from "react";
import { useMachine } from "@/features/machine/MachineContext";
import { errorMessage } from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon, { type IconName } from "@/shared/ui/Icon";
import Notice from "@/shared/ui/Notice";
import Panel from "@/shared/ui/Panel";
import type { Task, TaskAction } from "@/shared/types/api";
import { duration, label, number } from "@/shared/utils/format";
import { currentTask, STATUS_TONE } from "./taskState";

const ACTIONS: Record<string, { action: TaskAction; text: string; icon: IconName; variant: "primary" | "secondary" | "danger" }[]> = {
  assigned: [{ action: "start", text: "Start task", icon: "play_arrow", variant: "primary" }],
  in_progress: [
    { action: "pause", text: "Pause", icon: "pause", variant: "secondary" },
    { action: "block", text: "Mark blocked", icon: "block", variant: "secondary" },
  ],
  paused: [{ action: "resume", text: "Resume", icon: "play_arrow", variant: "primary" }],
  blocked: [{ action: "unblock", text: "Unblock", icon: "lock_open", variant: "primary" }],
};

function Progress({ task }: { task: Task }): React.ReactElement {
  const pct = task.target_quantity > 0 ? Math.min(100, (task.completed_quantity / task.target_quantity) * 100) : 0;
  return (
    <div className="rounded-lg border border-line bg-well p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-300">Progress</span>
        <span className="font-mono text-sm font-bold text-white">
          {number(task.completed_quantity, 1)} <span className="font-normal text-slate-400">/ {number(task.target_quantity, 0)} {label(task.quantity_unit)}</span>
        </span>
      </div>
      <div
        className="h-3 w-full overflow-hidden rounded-sm border border-line bg-panel-head"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <div className="h-full bg-brand transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1.5 font-mono text-xs font-bold text-white">{pct.toFixed(0)}% complete</div>
    </div>
  );
}

// Why the machine revised the estimate. Still one ETA number (rules.md).
const REVISION_NOTE: Record<string, string> = {
  weather: "Updated for the current weather",
  pace: "Updated for your current pace",
};

export default function CurrentTaskPanel(): React.ReactElement {
  const { tasks, runTaskAction, completionPromptTaskId, dismissCompletionPrompt } = useMachine();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const task = currentTask(tasks);

  const run = async (action: TaskAction) => {
    if (!task) return;
    setBusy(true);
    setProblem(null);
    try {
      await runTaskAction(task.task_id, action);
      setConfirming(false);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!task) {
    return (
      <Panel title="Current task" icon="assignment">
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <Icon name="task_alt" className="text-4xl text-slate-400" />
          <p className="font-display text-lg font-bold uppercase text-slate-200">No tasks assigned</p>
          <p className="text-sm text-slate-400">New work appears here once your supervisor assigns it.</p>
        </div>
      </Panel>
    );
  }

  const targetReached = task.completed_quantity >= task.target_quantity;
  const showPrompt = task.status === "in_progress" && (completionPromptTaskId === task.task_id || targetReached);

  return (
    <Panel
      title="Current task"
      icon="assignment"
      aside={<Badge tone={STATUS_TONE[task.status]}>{label(task.status)}</Badge>}
    >
      <div className="space-y-3.5">
        <div>
          <h3 className="font-display text-xl font-bold uppercase text-white">{label(task.task_type)}</h3>
          <p className="font-mono text-xs text-slate-400">
            Material: <strong className="text-slate-200">{label(task.material_type)}</strong>
          </p>
        </div>

        <Progress task={task} />

        <div className="flex items-center justify-between rounded-lg border border-brand/30 bg-brand/5 p-3">
          <div>
            <div className="font-mono text-[10px] font-bold uppercase text-brand">Estimated time</div>
            <div className="font-display text-xl font-bold text-white">{duration(task.eta_minutes)}</div>
            {task.eta_revision_reason && (
              <div className="mt-0.5 text-xs text-slate-300">{REVISION_NOTE[task.eta_revision_reason] ?? "Updated on the machine"}</div>
            )}
          </div>
          {task.eta_from_history && <Badge tone="neutral">Estimate based on history</Badge>}
        </div>

        {showPrompt && !confirming && (
          <Notice tone="ok" role="status">
            <p>Target quantity reached. Confirm the task is complete when the work is finished.</p>
            <div className="flex gap-2">
              <Button variant="primary" onClick={() => setConfirming(true)}>
                Confirm complete
              </Button>
              <Button variant="ghost" onClick={dismissCompletionPrompt}>
                Not yet
              </Button>
            </div>
          </Notice>
        )}

        {confirming && (
          <Notice tone="brand" role="status">
            <p>
              Mark this task as done? {number(task.completed_quantity, 1)} of {number(task.target_quantity, 0)}{" "}
              {label(task.quantity_unit)} recorded.
            </p>
            <div className="flex gap-2">
              <Button variant="primary" onClick={() => void run("complete")} disabled={busy}>
                Yes, task is done
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </Notice>
        )}

        {problem && (
          <Notice tone="critical" role="alert">
            <p>{problem}</p>
          </Notice>
        )}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {(ACTIONS[task.status] ?? []).map((a) => (
            <Button key={a.action} variant={a.variant} onClick={() => void run(a.action)} disabled={busy}>
              <Icon name={a.icon} className="text-base" /> {a.text}
            </Button>
          ))}
          {task.status === "in_progress" && !confirming && (
            <Button variant="secondary" onClick={() => setConfirming(true)} disabled={busy}>
              <Icon name="check" className="text-base" /> Complete
            </Button>
          )}
        </div>
      </div>
    </Panel>
  );
}
