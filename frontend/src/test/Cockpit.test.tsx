/**
 * Wiring tests for the operator screens: every value comes from the API or
 * the WebSocket, and every action calls the backend.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/app/App";
import {
  FakeSocket,
  NO_DATA,
  SHIFT,
  SIM_STATUS,
  SYSTEM_STATUS,
  incident,
  installFakeSocket,
  mockFetch,
  signIn,
  task,
} from "./harness";

const BASE_ROUTES = {
  "GET /api/operator/shift/current": SHIFT,
  "GET /api/operator/tasks": [task()],
  "GET /api/operator/incidents": [],
  "GET /api/operator/coaching": [],
  "GET /api/sim/status": SIM_STATUS,
  "GET /api/system/status": SYSTEM_STATUS,
  "GET /api/emergency": { notice: "n", items: [] },
};

function tick(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: "2026-09-23T08:00:05Z",
    machine_id: "m-1",
    task_id: null,
    engine_running: true,
    engine_rpm: 1234,
    engine_hours: 100,
    fuel_used: 1.5,
    hydraulic_active: true,
    machine_speed: 0,
    payload_pct: 64,
    seatbelt_status: "fastened",
    seat_occupied: true,
    park_brake: false,
    gear_state: "neutral",
    proximity_distance: null,
    proximity_sensor_ok: true,
    ambient_temp: 28,
    visibility: 400,
    tilt_angle: 3.5,
    ...overrides,
  };
}

describe("Cockpit wiring", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    installFakeSocket();
    signIn();
    window.history.pushState({}, "", "/");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the shift, operator, and task from the API", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    expect(await screen.findByText("Alex Beginner")).toBeInTheDocument();
    expect(screen.getByText("EX-320")).toBeInTheDocument();
    expect(screen.getAllByText("Excavation").length).toBeGreaterThan(0);
    expect(screen.getByText("1 h 35 min")).toBeInTheDocument();
  });

  it("opens the machine WebSocket and shows live readings from telemetry", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    await screen.findByText("Alex Beginner");
    await waitFor(() => expect(FakeSocket.latest?.url).toContain("/ws/operator/m-1?token=t"));

    FakeSocket.latest.open();
    FakeSocket.latest.push({ type: "telemetry", data: tick() as never });
    expect(await screen.findByText("1,234")).toBeInTheDocument();
    expect(screen.getAllByText("Working").length).toBeGreaterThan(0);
    expect(screen.queryByText("No live sensor data")).not.toBeInTheDocument();
  });

  it("shows No data, never OK, before any live telemetry", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    await screen.findByText("Alex Beginner");
    FakeSocket.latest.push({
      type: "state",
      data: { shift: SHIFT, tasks: [task()], incidents: [], coaching: [], status: NO_DATA },
    });
    expect(screen.getAllByText("No data").length).toBeGreaterThan(5);
    expect(screen.queryByText("OK")).not.toBeInTheDocument();
  });

  it("starts a task through the API", async () => {
    const calls = mockFetch({
      ...BASE_ROUTES,
      "POST /api/operator/tasks/t-1/start": task({ status: "in_progress" }),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /Start task/ }));
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/operator/tasks/t-1/start")).toBe(true);
    expect(await screen.findByRole("button", { name: /Pause/ })).toBeInTheDocument();
  });

  it("asks for confirmation before completing a task", async () => {
    const calls = mockFetch({
      ...BASE_ROUTES,
      "GET /api/operator/tasks": [task({ status: "in_progress" })],
      "POST /api/operator/tasks/t-1/complete": task({ status: "done" }),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Complete$/ }));
    expect(calls.some((c) => c.url.endsWith("/complete"))).toBe(false);

    await user.click(screen.getByRole("button", { name: "Yes, task is done" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/operator/tasks/t-1/complete")).toBe(true));
  });

  it("prompts for completion when the target is reached, without closing the task", async () => {
    const calls = mockFetch({ ...BASE_ROUTES, "GET /api/operator/tasks": [task({ status: "in_progress" })] });
    render(<App />);
    await screen.findByText("Alex Beginner");
    FakeSocket.latest.push({
      type: "progress",
      data: {
        task_id: "t-1", status: "in_progress", completed_quantity: 121, target_quantity: 120, target_reached: true,
        eta_minutes: 95, eta_revision_reason: null,
      },
    });
    expect(await screen.findByText(/Target quantity reached/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/complete"))).toBe(false);
  });

  it("acknowledges through the API and shows the backend reason when refused", async () => {
    mockFetch({
      ...BASE_ROUTES,
      "GET /api/operator/incidents": [incident()],
      "POST /api/operator/incidents/inc-1/acknowledge": () => ({
        status: 409,
        body: { error: { code: "ACK_REQUIRES_STATIONARY", message: "Stop the machine before acknowledging.", details: {} } },
      }),
    });
    const user = userEvent.setup();
    render(<App />);
    const alerts = await screen.findAllByRole("list", { name: "Open incidents" });
    await user.click(within(alerts[0]).getByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText("Stop the machine before acknowledging.")).toBeInTheDocument();
  });

  it("refreshes incidents when the engine pushes an incident event", async () => {
    let incidents: unknown[] = [];
    mockFetch({ ...BASE_ROUTES, "GET /api/operator/incidents": () => ({ body: incidents }) });
    render(<App />);
    await screen.findByText("Alex Beginner");
    incidents = [incident()];
    FakeSocket.latest.push({ type: "incident", data: { incident_id: "inc-1", event: "opened" } });
    expect((await screen.findAllByText(/Person or object close to the machine/)).length).toBeGreaterThan(0);
  });

  it("shows a training recommendation pushed by the backend", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    await screen.findByText("Alex Beginner");
    FakeSocket.latest.push({ type: "recommendation", data: { module_id: "working-near-people", module_title: "Working near people" } });
    expect(await screen.findByText(/Working near people\. Open it/)).toBeInTheDocument();
  });

  it("injects scenarios for this machine only", async () => {
    const calls = mockFetch({ ...BASE_ROUTES, "POST /api/sim/scenarios/proximity": {} });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /Person nearby/ }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/sim/scenarios/proximity?machine_id=m-1")).toBe(true)
    );
  });

  it("toggles the cloud link", async () => {
    const calls = mockFetch({ ...BASE_ROUTES, "POST /api/system/connectivity": { cloud_reachable: false } });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /Cut cloud link/ }));
    await waitFor(() => {
      const call = calls.find((c) => c.url === "/api/system/connectivity");
      expect(call?.body).toEqual({ cloud_reachable: false });
    });
  });

  it("hides presenter controls when the demo endpoints are not available", async () => {
    const { "GET /api/sim/status": _s, "GET /api/system/status": _y, ...routes } = BASE_ROUTES;
    mockFetch(routes);
    render(<App />);
    await screen.findByText("Alex Beginner");
    expect(screen.queryByRole("button", { name: /Start sim|Stop sim/ })).not.toBeInTheDocument();
  });

  it("shows an empty state when no shift is assigned", async () => {
    mockFetch({ ...BASE_ROUTES, "GET /api/operator/shift/current": () => ({ status: 404, body: { error: { code: "NOT_FOUND", message: "No current shift found." } } }) });
    render(<App />);
    expect(await screen.findByText("No shift assigned")).toBeInTheDocument();
  });

  it("files a manual incident report", async () => {
    const calls = mockFetch({ ...BASE_ROUTES, "POST /api/operator/incidents": incident({ incident_type: "manual_report" }) });
    window.history.pushState({}, "", "/safety");
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByRole("textbox"), "Near miss at loading point");
    await user.click(screen.getByRole("button", { name: "Submit report" }));
    await waitFor(() => {
      const call = calls.find((c) => c.method === "POST" && c.url === "/api/operator/incidents");
      expect(call?.body).toEqual({ description: "Near miss at loading point" });
    });
  });
});
