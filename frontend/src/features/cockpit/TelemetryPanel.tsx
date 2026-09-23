/** Live machine readings from the latest telemetry tick. */
import React from "react";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import Panel from "@/shared/ui/Panel";
import { label, number } from "@/shared/utils/format";

function Gauge({
  title,
  value,
  unit,
  pct,
  caption,
  alert = false,
}: {
  title: string;
  value: string;
  unit: string;
  pct: number | null;
  caption: string;
  alert?: boolean;
}): React.ReactElement {
  return (
    <div className={`rounded-lg border p-3.5 ${alert ? "border-red-500/80 bg-red-950/40" : "border-line bg-well"}`}>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-300">{title}</span>
        <span className="font-mono text-[11px] text-slate-400">{caption}</span>
      </div>
      <div className="flex items-baseline gap-1.5 font-mono text-3xl font-extrabold tracking-tight text-white">
        {value}
        <span className="font-display text-xs font-bold uppercase text-slate-400">{unit}</span>
      </div>
      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-sm border border-line bg-panel-head">
        <div
          className={`h-full transition-all duration-300 ${alert ? "bg-red-500" : "bg-brand"}`}
          style={{ width: `${pct === null ? 0 : Math.max(0, Math.min(100, pct))}%` }}
        />
      </div>
    </div>
  );
}

function Reading({ name, value }: { name: string; value: string }): React.ReactElement {
  return (
    <div className="rounded-lg border border-line bg-well p-3">
      <div className="font-display text-xs font-bold uppercase text-slate-400">{name}</div>
      <div className="mt-0.5 font-mono text-lg font-bold text-white">{value}</div>
    </div>
  );
}

export default function TelemetryPanel(): React.ReactElement {
  const { status, shift, incidents } = useMachine();
  const live = status?.live ?? false;
  const s = live ? status : null;
  const overloadOpen = incidents.some((i) => i.incident_type === "overloading" && i.status !== "resolved");
  const tiltOpen = incidents.some((i) => i.incident_type === "tilt" && i.status !== "resolved");
  const tiltLimit = shift?.tilt_limit_degrees ?? null;

  return (
    <Panel
      title="Machine readings"
      icon="speed"
      aside={<Badge tone={live ? "ok" : "warning"}>{live ? "Live" : "No live data"}</Badge>}
      footer={
        <>
          <span>Engine hours {number(s?.engine_hours ?? null, 1)}</span>
          <span>Fuel used this session {number(s?.fuel_used ?? null, 2, "L")}</span>
        </>
      }
    >
      <div className="space-y-3">
        <Gauge
          title="Engine speed"
          value={number(s?.engine_rpm ?? null, 0)}
          unit="rpm"
          pct={s?.engine_rpm != null && shift ? (s.engine_rpm / shift.rated_max_rpm) * 100 : null}
          caption={shift ? `Rated ${number(shift.rated_max_rpm, 0)}` : ""}
        />
        <Gauge
          title="Bucket load"
          value={number(s?.payload_pct ?? null, 0)}
          unit="% of rated"
          pct={s?.payload_pct ?? null}
          caption={shift ? `Bucket ${shift.bucket_capacity} m³` : ""}
          alert={overloadOpen}
        />
        <Gauge
          title="Tilt"
          value={number(s?.tilt_angle ?? null, 1)}
          unit="deg"
          pct={s?.tilt_angle != null && tiltLimit ? (s.tilt_angle / tiltLimit) * 100 : null}
          caption={tiltLimit ? `Limit ${tiltLimit}°` : ""}
          alert={tiltOpen}
        />
        <div className="grid grid-cols-2 gap-3">
          <Reading name="Ground speed" value={number(s?.machine_speed ?? null, 1, "km/h")} />
          <Reading name="Hydraulics" value={s ? (s.hydraulic_active ? "Working" : "Idle") : "No data"} />
          <Reading name="Park brake" value={s ? (s.park_brake ? "On" : "Off") : "No data"} />
          <Reading name="Gear" value={s ? label(s.gear_state) : "No data"} />
        </div>
      </div>
    </Panel>
  );
}
