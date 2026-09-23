import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../app/App";
import type { AdminShift, AdminTask } from "@/shared/types/api";
import { mockFetch, signIn, SIM_STATUS, SYSTEM_STATUS } from "./harness";

const OPERATORS = [
  { operator_id: "op-1", operator_name: "Alex Beginner", qualifications: [{ machine_type: "excavator", skill_level: "beginner" }] },
];
const MACHINES = [
  { machine_id: "m-1", machine_model: "EX-20", machine_type: "excavator", machine_status: "available", machine_age: 3, bucket_capacity: 1.2 },
];
const SHIFT: AdminShift = {
  shift_id: "sh-1",
  operator_id: "op-1",
  machine_id: "m-1",
  scheduled_start: "2026-09-23T07:00:00Z",
  scheduled_end: "2026-09-23T15:00:00Z",
  weather_forecast: "clear",
  weather_actual: null,
  delivery: "delivered",
};

function adminTask(overrides: Partial<AdminTask> = {}): AdminTask {
  return {
    task_id: "t-1",
    shift_id: "sh-1",
    task_type: "excavation",
    material_type: "clay",
    target_quantity: 40,
    quantity_unit: "m3",
    completed_quantity: 0,
    status: "assigned",
    operator_skill_at_assignment: "beginner",
    scheduled_start: "2026-09-23T07:00:00Z",
    scheduled_end: "2026-09-23T08:10:00Z",
    reassigned_at: null,
    actual_start: null,
    actual_end: null,
    raw_predicted_time: 55,
    planning_eta: 70,
    revised_predicted_time: null,
    is_fallback: false,
    model_version: "rf-1",
    aggregates_version: "agg-1",
    delivery: "queued",
    ...overrides,
  };
}

function adminRoutes(extra: Record<string, unknown> = {}): ReturnType<typeof mockFetch> {
  return mockFetch({
    "GET /api/admin/operators": OPERATORS,
    "GET /api/admin/machines": MACHINES,
    "GET /api/admin/shifts": [SHIFT],
    "GET /api/admin/shifts/sh-1/tasks": [adminTask()],
    "GET /api/sim/status": SIM_STATUS,
    "GET /api/system/status": { ...SYSTEM_STATUS, cloud_reachable: false, cloud_outbox_pending: 1 },
    ...extra,
  });
}

describe("Sign in", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("switches to PIN sign-in when the cloud is down and signs the operator in offline", async () => {
    const calls = mockFetch({
      "GET /api/system/health": { status: "ok", sim_time: "2026-09-23T08:00:00Z", cloud_reachable: true },
      "POST /api/auth/login": () => ({
        status: 503,
        body: { error: { code: "CLOUD_UNAVAILABLE", message: "The cloud connection is down. Sign in with your PIN instead.", details: {} } },
      }),
      "POST /api/auth/offline-login": {
        access_token: "tok", token_type: "bearer", role: "operator", user_id: "u-1", operator_id: "op-1", offline: true,
      },
    });
    const user = userEvent.setup();
    window.history.pushState({}, "", "/login");
    render(<App />);

    await user.type(screen.getByLabelText("Username"), "op_beginner");
    await user.type(screen.getByLabelText("Password"), "demo123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText(/Sign in with your PIN instead/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Shift PIN" })).toHaveAttribute("aria-pressed", "true");

    await user.type(screen.getByLabelText("PIN"), "1234");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const pinCall = calls.find((c) => c.url === "/api/auth/offline-login");
    expect(pinCall?.body).toEqual({ username: "op_beginner", pin: "1234" });
    expect(JSON.parse(sessionStorage.getItem("safe2go.session") ?? "{}")).toMatchObject({ offline: true, role: "operator" });
  });

  it("keeps an operator out of the admin screens", () => {
    signIn("operator");
    mockFetch({});
    window.history.pushState({}, "", "/admin");
    render(<App />);
    expect(window.location.pathname).toBe("/");
  });
});

describe("Admin assignments", () => {
  beforeEach(() => {
    sessionStorage.clear();
    signIn("admin");
    window.history.pushState({}, "", "/admin");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows queued delivery while the edge link is down", async () => {
    adminRoutes();
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Edge link down")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /Alex Beginner/ }));
    const tasks = await screen.findByText(/Excavation · 40/);
    const row = tasks.closest("li") as HTMLElement;
    expect(within(row).getByText("Queued")).toBeInTheDocument();
  });

  it("shows a rejected assignment inline with its reason", async () => {
    adminRoutes({
      "POST /api/admin/tasks": () => ({
        status: 409,
        body: { error: { code: "TASK_OVERLAP", message: "Task overlaps task T-1, planned 07:00 to 08:10.", details: {} } },
      }),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /Alex Beginner/ }));
    const form = await screen.findByRole("form", { name: "New task" });
    await user.click(within(form).getByRole("button", { name: "Assign task" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("Task overlaps task T-1");
  });

  it("shows the shift-end warning after a task is saved", async () => {
    const calls = adminRoutes({
      "POST /api/admin/tasks": {
        task: adminTask({ task_id: "t-2" }),
        warnings: [{ code: "SHIFT_END_EXCEEDED", message: "Planned finish is 25 min after the shift ends.", details: {} }],
      },
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /Alex Beginner/ }));
    const form = await screen.findByRole("form", { name: "New task" });
    await user.selectOptions(within(form).getByLabelText("Task type"), "material_loading");
    await user.click(within(form).getByRole("button", { name: "Assign task" }));

    expect(await screen.findByText(/Planned finish is 25 min after the shift ends/)).toBeInTheDocument();
    const sent = calls.find((c) => c.method === "POST" && c.url === "/api/admin/tasks");
    expect(sent?.body).toMatchObject({ task_type: "material_loading", quantity_unit: "loads" });
  });

  it("lists sync conflicts with a plain explanation", async () => {
    adminRoutes({
      "GET /api/admin/conflicts": [
        {
          conflict_id: "c-1", task_id: "t-1", conflict_type: "reassign_after_start",
          edge_actual_start: "2026-09-23T08:00:00Z", cloud_changed_at: "2026-09-23T08:05:00Z",
          created_at: "2026-09-23T08:06:00Z", resolved: false,
        },
      ],
    });
    window.history.pushState({}, "", "/admin/conflicts");
    render(<App />);
    expect(await screen.findByText(/The reassignment was undone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark reviewed" })).toBeInTheDocument();
  });
});
