/** Operator and demo-control endpoints. All calls go through the shared client. */
import { apiClient } from "./client";
import type {
  Coaching,
  ExplainResponse,
  ServiceSuggestion,
  ShiftReport,
  Incident,
  ManualSearchResponse,
  ScenarioName,
  Shift,
  SimStatus,
  SystemStatus,
  Task,
  TaskAction,
} from "@/shared/types/api";

export const operatorApi = {
  currentShift: (token: string) => apiClient.get<Shift>("/api/operator/shift/current", { token }),
  tasks: (token: string) => apiClient.get<Task[]>("/api/operator/tasks", { token }),
  incidents: (token: string) => apiClient.get<Incident[]>("/api/operator/incidents", { token }),
  coaching: (token: string) => apiClient.get<Coaching[]>("/api/operator/coaching", { token }),

  taskAction: (token: string, taskId: string, action: TaskAction) =>
    apiClient.post<Task>(`/api/operator/tasks/${encodeURIComponent(taskId)}/${action}`, undefined, { token }),

  acknowledge: (token: string, incidentId: string) =>
    apiClient.post<Incident>(
      `/api/operator/incidents/${encodeURIComponent(incidentId)}/acknowledge`,
      undefined,
      { token }
    ),

  reportIncident: (token: string, description: string) =>
    apiClient.post<Incident>("/api/operator/incidents", { description }, { token }),

  maintenance: (token: string) => apiClient.get<ServiceSuggestion>("/api/operator/maintenance", { token }),
  shiftSummary: (token: string) => apiClient.get<ShiftReport>("/api/operator/shift/summary", { token }),

  explain: (token: string, question: string) =>
    apiClient.post<ExplainResponse>("/api/assistant/explain", { question }, { token }),

  searchManuals: (token: string, query: string) =>
    apiClient.get<ManualSearchResponse>(
      `/api/assistant/search?${new URLSearchParams({ q: query })}`,
      { token }
    ),
};

/** Simulator and connectivity controls. Available only when the backend runs in dev or demo mode. */
export const demoApi = {
  simStatus: () => apiClient.get<SimStatus>("/api/sim/status"),
  systemStatus: () => apiClient.get<SystemStatus>("/api/system/status"),
  start: () => apiClient.post<SimStatus>("/api/sim/start"),
  stop: () => apiClient.post<SimStatus>("/api/sim/stop"),
  setSpeed: (speed: number) => apiClient.post("/api/sim/speed", { speed }),
  inject: (scenario: ScenarioName, machineId: string) =>
    apiClient.post(`/api/sim/scenarios/${scenario}?${new URLSearchParams({ machine_id: machineId })}`),
  setCloudReachable: (reachable: boolean) =>
    apiClient.post("/api/system/connectivity", { cloud_reachable: reachable }),
};

export function operatorSocketUrl(machineId: string, token: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ token });
  return `${scheme}://${window.location.host}/ws/operator/${encodeURIComponent(machineId)}?${query}`;
}
