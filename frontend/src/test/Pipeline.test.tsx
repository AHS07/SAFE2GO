import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../app/App";
import type { MachineRollup, PipelineStatus } from "@/shared/types/api";
import { installFakeSocket, mockFetch, SHIFT, signIn, SIM_STATUS, SYSTEM_STATUS, task } from "./harness";

const MACHINES = [
  {
    machine_id: "m-1", machine_model: "EX-20", machine_type: "excavator", machine_status: "available",
    machine_age: 3, bucket_capacity: 1.2,
    service: { status: "ok", hours_since_service: 10, service_interval_hours: 500, message: null },
  },
];

function status(overrides: Partial<PipelineStatus> = {}): PipelineStatus {
  return {
    enabled: true,
    bootstrap_servers: "localhost:9092",
    topics: ["safe2go.telemetry.raw", "safe2go.safety.events"],
    forwarder: {
      state: "idle", sent_total: 1200, failed_total: 0, dropped_total: 0, gaps_total: 0,
      send_rate: 0, arrival_rate: 3, max_rate: 2000, last_sent_at: "2026-09-23T08:00:00Z",
      last_error: null, last_error_at: null, retry_in_seconds: null,
    },
    spool: {
      total: 0, by_kind: {}, by_machine: [], oldest_age_seconds: null, bytes: 16384, limit: 250000,
      drain_eta_seconds: 0, write_failures: 0,
    },
    consumer: {
      state: "running", received_total: 1200, stored_total: 1195, duplicates_total: 5, malformed_total: 0,
      lag: 0, last_batch_at: "2026-09-23T08:00:00Z", last_error: null, last_error_at: null,
    },
    archive: {
      ticks: 1195, machines: 1, first_ts: "2026-09-23T07:00:00Z", last_ts: "2026-09-23T08:00:00Z",
      dropped_total: 0, recent_gaps: [], events_by_type: [{ kind: "incident", event_type: "proximity", count: 3 }],
    },
    ...overrides,
  };
}

const ROLLUPS: MachineRollup[] = [
  {
    machine_id: "m-1",
    points: [
      { bucket_start: "2026-09-23T07:58:00Z", ticks: 60, engine_on_ticks: 60, working_ticks: 45, idle_ticks: 15, avg_rpm: 1500, fuel_used: 0.4, load_cycles: 2 },
      { bucket_start: "2026-09-23T07:59:00Z", ticks: 60, engine_on_ticks: 60, working_ticks: 30, idle_ticks: 30, avg_rpm: 1100, fuel_used: 0.2, load_cycles: 1 },
    ],
  },
];

function routes(pipeline: PipelineStatus, extra: Record<string, unknown> = {}): void {
  mockFetch({
    "GET /api/admin/operators": [],
    "GET /api/admin/machines": MACHINES,
    "GET /api/admin/conflicts": [],
    "GET /api/admin/sync/dead-letters": [],
    "GET /api/admin/pipeline/rollups": ROLLUPS,
    "GET /api/admin/pipeline": pipeline,
    "GET /api/sim/status": SIM_STATUS,
    "GET /api/system/status": SYSTEM_STATUS,
    ...extra,
  });
}

describe("Data pipeline page", () => {
  beforeEach(() => {
    sessionStorage.clear();
    signIn("admin");
    window.history.pushState({}, "", "/admin/pipeline");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains how to turn streaming on when it is off", async () => {
    routes(status({ enabled: false }));
    render(<App />);
    expect(await screen.findByText(/Streaming is off/)).toHaveTextContent("KAFKA_ENABLED=true");
  });

  it("shows a broker outage with the spooled backlog per machine", async () => {
    routes(status({
      forwarder: {
        ...status().forwarder, state: "broker_down", last_error: "Kafka unreachable: connection refused",
        retry_in_seconds: 8,
      },
      spool: {
        ...status().spool, total: 4200, by_kind: { telemetry: 4200 },
        by_machine: [{ machine_id: "m-1", records: 4200, oldest_event_time: "2026-09-23T07:00:00Z" }],
        oldest_age_seconds: 95, drain_eta_seconds: null,
      },
    }));
    render(<App />);

    expect(await screen.findByText("Kafka unreachable, spooling")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("connection refused");
    expect(screen.getByRole("alert")).toHaveTextContent("Retrying in 8 s");
    expect(screen.getByText("1 min 35 s")).toBeInTheDocument();
    const waiting = screen.getByText("Waiting on the machines").closest("section") ?? document.body;
    expect(within(waiting as HTMLElement).getByText("EX-20")).toBeInTheDocument();
    expect(screen.getByText("not shrinking yet")).toBeInTheDocument();
  });

  it("charts per-minute activity with a tooltip and a totals table", async () => {
    routes(status());
    render(<App />);

    const chart = await screen.findByRole("img", { name: /EX-20: working and idle/ });
    const hitTargets = chart.querySelectorAll("rect[fill-opacity='0']");
    expect(hitTargets).toHaveLength(2);
    fireEvent.mouseEnter(hitTargets[1]);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Working 30 s");
    expect(tooltip).toHaveTextContent("Idle 30 s");

    const totals = screen.getByRole("table", { name: "Fleet activity totals" });
    // 75 working and 45 idle of 120 ticks.
    expect(within(totals).getByText("63%")).toBeInTheDocument();
    expect(within(totals).getByText("38%")).toBeInTheDocument();
    expect(screen.getByText("Proximity")).toBeInTheDocument();
  });

  it("reports dropped ticks as gaps instead of hiding them", async () => {
    routes(status({
      archive: {
        ...status().archive,
        dropped_total: 500,
        recent_gaps: [{
          machine_id: "m-1", from_ts: "2026-09-23T06:00:00Z", to_ts: "2026-09-23T06:08:19Z",
          dropped_count: 500, dropped_at: "2026-09-23T07:00:00Z",
        }],
      },
    }));
    render(<App />);
    expect(await screen.findByText(/oldest raw ticks were dropped/)).toBeInTheDocument();
    expect(screen.getByText(/EX-20: 500 ticks from/)).toBeInTheDocument();
  });
});

describe("Demo bar", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows ticks spooled for fleet analytics on the operator cockpit", async () => {
    sessionStorage.clear();
    localStorage.clear();
    installFakeSocket();
    signIn();
    window.history.pushState({}, "", "/");
    mockFetch({
      "GET /api/operator/shift/current": SHIFT,
      "GET /api/operator/tasks": [task()],
      "GET /api/operator/incidents": [],
      "GET /api/operator/coaching": [],
      "GET /api/sim/status": SIM_STATUS,
      "GET /api/system/status": { ...SYSTEM_STATUS, stream_spool_pending: 1234 },
      "GET /api/emergency": { notice: "n", items: [] },
    });
    render(<App />);
    expect(await screen.findByText(/1,234 spooled for fleet analytics/)).toBeInTheDocument();
  });
});
