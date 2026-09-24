/**
 * Status strip plus presenter controls. Scenario buttons only change raw
 * sensor values in the simulator; the backend safety and behavior engines
 * decide what becomes an alert.
 */
import React from "react";
import { useMachine } from "@/features/machine/MachineContext";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon, { type IconName } from "@/shared/ui/Icon";
import type { ScenarioName } from "@/shared/types/api";
import { label } from "@/shared/utils/format";
import { useDemo } from "./DemoContext";

const SCENARIOS: { name: ScenarioName; label: string; icon: IconName }[] = [
  { name: "proximity", label: "Person nearby", icon: "radar" },
  { name: "sensor_fault", label: "Sensor fault", icon: "sensors_off" },
  { name: "overload", label: "Overload", icon: "weight" },
  { name: "seatbelt", label: "Seatbelt off", icon: "airline_seat_recline_extra" },
  { name: "tilt", label: "Tilt", icon: "screen_rotation" },
  { name: "idling", label: "Idling", icon: "hourglass_empty" },
  // Runs the end-of-shift idle ratio check; the live demo has no real shift end.
  { name: "end_shift", label: "End shift", icon: "event_available" },
];

const SPEEDS = [1, 5, 10, 20];

function SyncBadge(): React.ReactElement | null {
  const { cloudReachable, pending } = useDemo();
  if (cloudReachable === null || pending === null) return null;
  const waiting = pending.edge + pending.cloud;
  if (cloudReachable && waiting === 0) return null;
  return (
    <Badge tone="warning">
      <Icon name="sync" className="text-sm" />
      {cloudReachable ? `Syncing ${waiting}` : `Cloud offline, ${pending.edge} records waiting to sync`}
    </Badge>
  );
}

/** Live ticks held on the machine for Kafka; they drain, paced, once the link is back. */
function SpoolBadge(): React.ReactElement | null {
  const { pending } = useDemo();
  const spooled = pending?.stream ?? 0;
  if (spooled === 0) return null;
  return (
    <Badge tone="info">
      <Icon name="pending_actions" className="text-sm" />
      {spooled.toLocaleString()} spooled for fleet analytics
    </Badge>
  );
}

function StatusStrip(): React.ReactElement {
  const { incidents, status, degradedRules } = useMachine();
  const open = incidents.filter((i) => i.status !== "resolved").length;
  const live = status?.live ?? false;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={open > 0 ? "critical" : "ok"}>
        <span className={`h-2 w-2 rounded-full pulse-dot ${open > 0 ? "bg-red-500" : "bg-emerald-400"}`} />
        {open > 0 ? `${open} open incident${open > 1 ? "s" : ""}` : "No open incidents"}
      </Badge>
      {live ? (
        <>
          <Badge tone="neutral">Engine {status?.engine_running ? "running" : "off"}</Badge>
          <Badge tone={status?.parked ? "info" : "brand"}>{status?.parked ? "Parked" : "Working"}</Badge>
        </>
      ) : (
        <Badge tone="warning">No live sensor data</Badge>
      )}
      <SyncBadge />
      <SpoolBadge />
      {degradedRules.map((rule) => (
        <Badge key={rule} tone="warning">
          {label(rule)} check unavailable
        </Badge>
      ))}
    </div>
  );
}

function PresenterControls(): React.ReactElement | null {
  const demo = useDemo();
  const { shift } = useMachine();
  if (!demo.available || !demo.sim) return null;
  const { sim } = demo;
  const machineId = shift?.machine_id;
  const active = new Set(sim.scenarios.filter((s) => !s.machine_id || s.machine_id === machineId).map((s) => s.scenario));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-400">Demo:</span>
      {sim.running ? (
        <Button compact onClick={() => void demo.stop()} disabled={demo.busy}>
          <Icon name="stop" className="text-sm" /> Stop sim
        </Button>
      ) : (
        <Button compact variant="primary" onClick={() => void demo.start()} disabled={demo.busy}>
          <Icon name="play_arrow" className="text-sm" /> Start sim
        </Button>
      )}
      <label className="flex items-center gap-1 font-mono text-[11px] uppercase text-slate-400">
        Speed
        <select
          value={sim.selected_speed}
          onChange={(e) => void demo.setSpeed(Number(e.target.value))}
          disabled={demo.busy}
          className="min-h-9 rounded border border-line bg-raised px-2 font-mono text-xs text-slate-100"
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}x
            </option>
          ))}
        </select>
      </label>
      {SCENARIOS.map((scenario) => (
        <Button
          key={scenario.name}
          compact
          variant={active.has(scenarioWindowName(scenario.name)) ? "danger" : "secondary"}
          disabled={demo.busy || !sim.running || !machineId}
          onClick={() => machineId && void demo.inject(scenario.name, machineId)}
          title={sim.running ? `Inject: ${scenario.label}` : "Start the simulator first"}
        >
          <Icon name={scenario.icon} className="text-sm" /> {scenario.label}
        </Button>
      ))}
      {demo.cloudReachable !== null && (
        <Button
          compact
          variant={demo.cloudReachable ? "secondary" : "danger"}
          disabled={demo.busy}
          onClick={() => void demo.setCloudReachable(!demo.cloudReachable)}
        >
          <Icon name={demo.cloudReachable ? "cloud_off" : "cloud_done"} className="text-sm" />
          {demo.cloudReachable ? "Cut cloud link" : "Restore cloud link"}
        </Button>
      )}
    </div>
  );
}

// The backend names scenario windows slightly differently from the route names.
function scenarioWindowName(name: ScenarioName): string {
  const windows: Record<ScenarioName, string> = {
    proximity: "proximity_breach",
    sensor_fault: "proximity_sensor_fault",
    overload: "overload",
    seatbelt: "seatbelt_removal",
    idling: "excessive_idling",
    tilt: "tilt",
    end_shift: "end_shift",
  };
  return windows[name];
}

export default function DemoControlBar(): React.ReactElement {
  const { error } = useDemo();
  return (
    <section className="mx-auto w-full max-w-[1780px] px-4 pb-1 pt-3 lg:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-panel p-3 shadow-hud">
        <StatusStrip />
        <PresenterControls />
      </div>
      {error !== null && (
        <div className="mt-2">
          <ErrorNotice error={error} />
        </div>
      )}
    </section>
  );
}
