/** Session 12 fixes: proximity sensor fault shown as unknown, ETA reason, dead letters. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/app/App";
import { FakeSocket, installFakeSocket, mockFetch, SHIFT, signIn, SIM_STATUS, SYSTEM_STATUS, task } from "./harness";

const BASE_ROUTES = {
  "GET /api/operator/shift/current": SHIFT,
  "GET /api/operator/tasks": [task({ status: "in_progress" })],
  "GET /api/operator/incidents": [],
  "GET /api/operator/coaching": [],
  "GET /api/sim/status": SIM_STATUS,
  "GET /api/system/status": SYSTEM_STATUS,
  "GET /api/emergency": { notice: "n", items: [] },
};

function tick(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: "2026-09-23T08:00:05Z", machine_id: "m-1", task_id: null, engine_running: true, engine_rpm: 1234,
    engine_hours: 100, fuel_used: 1.5, hydraulic_active: true, machine_speed: 0, payload_pct: 64,
    seatbelt_status: "fastened", seat_occupied: true, park_brake: false, gear_state: "neutral",
    proximity_distance: null, proximity_sensor_ok: true, ambient_temp: 28, visibility: 400, tilt_angle: 3.5,
    ...overrides,
  };
}

async function cockpit(): Promise<void> {
  signIn();
  installFakeSocket();
  mockFetch(BASE_ROUTES);
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByText("Alex Beginner");
  await waitFor(() => expect(FakeSocket.latest).toBeDefined());
  FakeSocket.latest.open();
}

describe("Operator view", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a proximity sensor fault as unknown, never as nothing in range", async () => {
    await cockpit();
    FakeSocket.latest.push({ type: "telemetry", data: tick({ proximity_sensor_ok: false }) as never });
    // The demo bar also has a "Sensor fault" button; the reading is the non-button text.
    await waitFor(() => {
      const readings = screen.queryAllByText("Sensor fault").filter((el) => !el.closest("button"));
      expect(readings.length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Nothing in range")).not.toBeInTheDocument();
  });

  it("raises and clears the check-unavailable badge with the engine's messages", async () => {
    await cockpit();
    FakeSocket.latest.push({ type: "safety_degraded", data: { rule: "proximity", reason: "sensor_fault" } });
    expect(await screen.findByText("Proximity check unavailable")).toBeInTheDocument();
    FakeSocket.latest.push({ type: "safety_restored", data: { rule: "proximity" } });
    await waitFor(() => expect(screen.queryByText("Proximity check unavailable")).not.toBeInTheDocument());
  });

  it("says why the ETA changed, next to the one ETA number", async () => {
    await cockpit();
    FakeSocket.latest.push({
      type: "progress",
      data: {
        task_id: "t-1", status: "in_progress", completed_quantity: 40, target_quantity: 120, target_reached: false,
        eta_minutes: 130, eta_revision_reason: "pace",
      },
    });
    expect(await screen.findByText("Updated for your current pace")).toBeInTheDocument();
    expect(screen.getAllByText("2 h 10 min").length).toBeGreaterThan(0);
  });
});

describe("Admin sync page", () => {
  beforeEach(() => {
    sessionStorage.clear();
    signIn("admin");
    window.history.pushState({}, "", "/admin/conflicts");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists dead letters and retries one", async () => {
    const calls = mockFetch({
      "GET /api/admin/conflicts": [],
      "GET /api/admin/sync/dead-letters": [{
        message_id: "msg-1", direction: "edge_to_cloud", message_type: "incident", entity_id: "inc-12345678",
        attempts: 64, last_error: "IntegrityError: duplicate key", created_at: "2026-09-23T08:00:00Z",
        first_failed_at: "2026-09-23T08:00:01Z", dead_at: "2026-09-23T09:00:02Z",
      }],
      "POST /api/admin/sync/dead-letters/msg-1/retry": { message_id: "msg-1", direction: "edge_to_cloud" },
      "GET /api/sim/status": SIM_STATUS,
      "GET /api/system/status": SYSTEM_STATUS,
    });
    const user = userEvent.setup();
    render(<App />);

    const row = (await screen.findByText(/Incident · Machine to cloud/)).closest("li") as HTMLElement;
    expect(within(row).getByText(/64 attempts/)).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Retry" }));
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/admin/sync/dead-letters/msg-1/retry")).toBe(true);
  });
});
