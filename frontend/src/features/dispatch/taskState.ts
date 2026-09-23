import type { Tone } from "@/shared/ui/Badge";
import type { Task, TaskStatus } from "@/shared/types/api";

export const STATUS_TONE: Record<TaskStatus, Tone> = {
  assigned: "info",
  in_progress: "brand",
  paused: "warning",
  blocked: "critical",
  done: "ok",
  cancelled: "neutral",
};

// A started task stays current until it is done; otherwise the next assigned one.
const CURRENT_ORDER: TaskStatus[] = ["in_progress", "blocked", "paused", "assigned"];

export function currentTask(tasks: Task[]): Task | null {
  for (const status of CURRENT_ORDER) {
    const task = tasks.find((t) => t.status === status);
    if (task) return task;
  }
  return null;
}
