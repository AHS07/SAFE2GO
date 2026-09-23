/** Test helpers: a routed fetch mock, a controllable WebSocket, and fixtures. */
import { act } from "@testing-library/react";
import { vi } from "vitest";
import type { Incident, MachineStatus, ServerMessage, Shift, Task } from "@/shared/types/api";

type Handler = (body: unknown, url: string) => { status?: number; body: unknown };

export interface FetchCall {
  method: string;
  url: string;
  body: unknown;
}

/** Routes are keyed "METHOD /path" and match on the path prefix, ignoring the query string. */
export function mockFetch(routes: Record<string, Handler | unknown>): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url: input, body });
      const path = input.split("?")[0];
      const key = Object.keys(routes)
        .filter((k) => {
          const [m, p] = k.split(" ");
          return m === method && path.startsWith(p);
        })
        .sort((a, b) => b.length - a.length)[0];
      if (!key) return json({ error: { code: "NOT_FOUND", message: "Not found." } }, 404);
      const route = routes[key];
      const result = typeof route === "function" ? (route as Handler)(body, input) : { body: route };
      return json(result.body, result.status ?? 200);
    })
  );
  return calls;
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Replaces the global WebSocket. Tests push server messages with FakeSocket.latest.send(). */
export class FakeSocket {
  static instances: FakeSocket[] = [];
  static get latest(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }
  static readonly OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public readonly url: string) {
    FakeSocket.instances.push(this);
  }

  open(): void {
    act(() => {
      this.readyState = FakeSocket.OPEN;
      this.onopen?.();
    });
  }

  push(message: ServerMessage): void {
    act(() => this.onmessage?.({ data: JSON.stringify(message) }));
  }

  send(): void {}
  close(): void {
    this.readyState = 3;
  }
}

export function installFakeSocket(): void {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
}

export function signIn(role: "operator" | "admin" = "operator"): void {
  const operatorId = role === "operator" ? "op-1" : null;
  sessionStorage.setItem("safe2go.session", JSON.stringify({ token: "t", role, operatorId, offline: false }));
}

export const SHIFT: Shift = {
  shift_id: "sh-1",
  machine_id: "m-1",
  operator_id: "op-1",
  operator_name: "Alex Beginner",
  scheduled_start: "2026-09-23T07:00:00Z",
  scheduled_end: "2026-09-23T15:00:00Z",
  weather_forecast: "clear",
  machine_model: "EX-320",
  machine_type: "excavator",
  bucket_capacity: 1.2,
  tilt_limit_degrees: 30,
  rated_max_rpm: 1800,
};

export function task(overrides: Partial<Task> = {}): Task {
  return {
    task_id: "t-1",
    shift_id: "sh-1",
    task_type: "excavation",
    material_type: "clay",
    target_quantity: 120,
    quantity_unit: "m3",
    completed_quantity: 30,
    status: "assigned",
    scheduled_start: "2026-09-23T07:00:00Z",
    scheduled_end: "2026-09-23T09:00:00Z",
    eta_minutes: 95,
    eta_from_history: false,
    actual_start: null,
    actual_end: null,
    ...overrides,
  };
}

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    incident_id: "inc-1",
    incident_type: "proximity",
    peak_severity: "critical",
    status: "open",
    source: "engine",
    event_start: "2026-09-23T08:00:00Z",
    event_end: null,
    escalation_reason: "threshold",
    description: null,
    acknowledged_at: null,
    ...overrides,
  };
}

export const NO_DATA: MachineStatus = {
  machine_id: "m-1", live: false, parked: false, timestamp: null, engine_running: null, engine_rpm: null,
  engine_hours: null, fuel_used: null, hydraulic_active: null, machine_speed: null, payload_pct: null,
  seatbelt_status: null, seat_occupied: null, park_brake: null, gear_state: null, proximity_distance: null,
  ambient_temp: null, visibility: null, tilt_angle: null, task_id: null,
};

export const SIM_STATUS = {
  running: true,
  sim_time: "2026-09-23T08:00:00Z",
  selected_speed: 1,
  effective_speed: 1,
  machine_ids: ["m-1"],
  scenarios: [],
};

export const SYSTEM_STATUS = {
  status: "ok",
  sim_time: "2026-09-23T08:00:00Z",
  cloud_reachable: true,
  app_env: "dev",
  cloud_outbox_pending: 0,
  edge_outbox_pending: 0,
};
