/** Phase 9 screens: explanations with fallback, service suggestion, shift summary. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/app/App";
import type { ShiftReport } from "@/shared/types/api";
import { installFakeSocket, mockFetch, SHIFT, signIn, SIM_STATUS, SYSTEM_STATUS, task } from "./harness";

const BASE_ROUTES = {
  "GET /api/operator/shift/current": SHIFT,
  "GET /api/operator/tasks": [task()],
  "GET /api/operator/incidents": [],
  "GET /api/operator/coaching": [],
  "GET /api/sim/status": SIM_STATUS,
  "GET /api/system/status": SYSTEM_STATUS,
  "GET /api/emergency": { notice: "n", items: [] },
};

const HIT = {
  manual_id: "ex",
  manual_title: "Sample hydraulic excavator manual",
  machine_type: "excavator",
  section_title: "Starting the engine",
  text: "Fasten the seatbelt. Put the hydraulic lockout lever in the locked position.",
  score: 0.42,
};

const REPORT: ShiftReport = {
  shift_id: "sh-1",
  operator_name: "Alex Beginner",
  machine_model: "EX-20",
  scheduled_start: "2026-09-23T07:00:00Z",
  scheduled_end: "2026-09-23T15:00:00Z",
  source: "edge",
  tasks_done: 1,
  tasks_total: 2,
  tasks: [
    { task_id: "t-1", task_type: "excavation", status: "done", target_quantity: 120, completed_quantity: 121, quantity_unit: "m3", working_minutes: 95 },
  ],
  time: { engine_hours: 2, working_minutes: 90, idle_minutes: 30, idle_ratio: 0.25, cycle_count: 40, fuel_used: 31 },
  incidents_total: 2,
  incidents_critical: 1,
  incidents: [{ type: "proximity", count: 2 }],
  behavior: [{ type: "excessive_idling", count: 1 }],
  training: [{ module_id: "m1", title: "Working near people" }],
};

function start(path: string, routes: Record<string, unknown>): ReturnType<typeof mockFetch> {
  signIn();
  installFakeSocket();
  const calls = mockFetch({ ...BASE_ROUTES, ...routes });
  window.history.pushState({}, "", path);
  render(<App />);
  return calls;
}

describe("Phase 9 operator screens", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an explanation above the manual sections it came from", async () => {
    const calls = start("/manuals", {
      "POST /api/assistant/explain": {
        query: "How do I start?", results: [HIT], explanation: "1. Fasten the seatbelt.\n2. Lock the hydraulics.",
        notice: null, model: "deepseek-chat",
      },
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search the manuals"), "How do I start?");
    await user.click(screen.getByRole("button", { name: "Explain" }));

    const explanation = await screen.findByRole("region", { name: "Explanation" });
    expect(explanation).toHaveTextContent("Fasten the seatbelt.");
    expect(screen.getByText("Starting the engine")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/assistant/explain")?.body).toEqual({ question: "How do I start?" });
  });

  it("falls back to the manual sections with a notice", async () => {
    start("/manuals", {
      "POST /api/assistant/explain": {
        query: "How do I start?", results: [HIT], explanation: null,
        notice: "The cloud connection is down, so explanations are not available. The manual sections are shown below.",
        model: null,
      },
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search the manuals"), "How do I start?");
    await user.click(screen.getByRole("button", { name: "Explain" }));

    expect(await screen.findByText(/explanations are not available/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Explanation" })).not.toBeInTheDocument();
    expect(screen.getByText("Starting the engine")).toBeInTheDocument();
  });

  it("shows a service suggestion on the cockpit when service is due", async () => {
    start("/", {
      "GET /api/operator/maintenance": {
        status: "due_soon", engine_hours: 1460, hours_since_service: 460, hours_remaining: 40,
        service_interval_hours: 500, message: "Service due in 40 engine hours. Book it with maintenance.",
      },
    });
    expect(await screen.findByText(/Service due in 40 engine hours/)).toBeInTheDocument();
    expect(screen.getByText(/460 of 500 engine hours/)).toBeInTheDocument();
  });

  it("shows nothing about service when none is due", async () => {
    start("/", {
      "GET /api/operator/maintenance": {
        status: "ok", engine_hours: 1100, hours_since_service: 100, hours_remaining: 400,
        service_interval_hours: 500, message: null,
      },
    });
    await screen.findByText("Alex Beginner", { exact: false });
    expect(screen.queryByText(/Service due/)).not.toBeInTheDocument();
  });

  it("summarises the shift so far", async () => {
    start("/summary", { "GET /api/operator/shift/summary": REPORT });
    expect(await screen.findByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByText("1 h 30 min")).toBeInTheDocument();
    expect(screen.getByText("Working near people")).toBeInTheDocument();
    expect(screen.getByText("1 critical")).toBeInTheDocument();
  });
});
