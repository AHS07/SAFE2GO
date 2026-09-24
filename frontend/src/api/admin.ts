/** Admin (cloud) endpoints. All calls go through the shared client. */
import { apiClient } from "./client";
import type {
  AdminMachine,
  AdminOperator,
  AdminShift,
  AdminTask,
  AssignmentResponse,
  DeadLetter,
  EtaBreakdown,
  MachineRollup,
  OperatorAccount,
  OperatorAccountCreate,
  PipelineStatus,
  ShiftCreate,
  ShiftCreateResponse,
  ShiftReport,
  SyncConflict,
  TaskCreate,
} from "@/shared/types/api";

const id = encodeURIComponent;

export const adminApi = {
  operators: (token: string) => apiClient.get<AdminOperator[]>("/api/admin/operators", { token }),
  createOperatorAccount: (token: string, operatorId: string, body: OperatorAccountCreate) =>
    apiClient.post<OperatorAccount>(`/api/admin/operators/${id(operatorId)}/account`, body, { token }),
  machines: (token: string) => apiClient.get<AdminMachine[]>("/api/admin/machines", { token }),
  shifts: (token: string) => apiClient.get<AdminShift[]>("/api/admin/shifts?limit=50", { token }),
  shiftTasks: (token: string, shiftId: string) =>
    apiClient.get<AdminTask[]>(`/api/admin/shifts/${id(shiftId)}/tasks`, { token }),
  createShift: (token: string, body: ShiftCreate) =>
    apiClient.post<ShiftCreateResponse>("/api/admin/shifts", body, { token }),
  createTask: (token: string, body: TaskCreate) =>
    apiClient.post<AssignmentResponse>("/api/admin/tasks", body, { token }),
  cancelTask: (token: string, taskId: string) =>
    apiClient.post<AdminTask>(`/api/admin/tasks/${id(taskId)}/cancel`, undefined, { token }),
  reassignTask: (token: string, taskId: string, targetShiftId: string) =>
    apiClient.post<AssignmentResponse>(
      `/api/admin/tasks/${id(taskId)}/reassign`,
      { target_shift_id: targetShiftId },
      { token }
    ),
  shiftSummary: (token: string, shiftId: string) =>
    apiClient.get<ShiftReport>(`/api/admin/shifts/${id(shiftId)}/summary`, { token }),
  eta: (token: string, taskId: string) => apiClient.get<EtaBreakdown>(`/api/admin/tasks/${id(taskId)}/eta`, { token }),
  conflicts: (token: string) =>
    apiClient.get<SyncConflict[]>("/api/admin/conflicts?include_resolved=true", { token }),
  deadLetters: (token: string) => apiClient.get<DeadLetter[]>("/api/admin/sync/dead-letters", { token }),
  retryDeadLetter: (token: string, messageId: string) =>
    apiClient.post<{ message_id: string; direction: string }>(
      `/api/admin/sync/dead-letters/${id(messageId)}/retry`,
      undefined,
      { token }
    ),
  pipeline: (token: string) => apiClient.get<PipelineStatus>("/api/admin/pipeline", { token }),
  pipelineRollups: (token: string, minutes: number) =>
    apiClient.get<MachineRollup[]>(`/api/admin/pipeline/rollups?minutes=${minutes}`, { token }),
  resolveConflict: (token: string, conflictId: string) =>
    apiClient.post<SyncConflict>(`/api/admin/conflicts/${id(conflictId)}/resolve`, undefined, { token }),
};
