/**
 * Proximity view. The sensor reports one nearest distance with no
 * direction (prd.md section 8), so the live reading is drawn as a ring
 * around the machine, not as a blip at a bearing. Alert state comes from
 * the backend proximity incident. The nearby asset list is illustrative.
 */
import React from "react";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import IllustrativeTag from "@/shared/ui/IllustrativeTag";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import { number } from "@/shared/utils/format";

// Display range of the scope in metres, with distance marks every 5 m.
const RANGE_M = 20;
const MARKS_M = [5, 10, 15, 20];

const SAMPLE_ASSETS = [
  { id: "HT-09", name: "Haul truck", range: "14 m", status: "At loading point" },
  { id: "WL-04", name: "Wheel loader", range: "48 m", status: "Stockpile" },
  { id: "HT-12", name: "Haul truck", range: "112 m", status: "On haul road" },
];

function Scope({ distance, tone }: { distance: number | null; tone: "critical" | "warning" | "ok" | "neutral" }) {
  const ringPct = distance === null ? null : Math.min(100, (distance / RANGE_M) * 100);
  const ringColor = {
    critical: "border-red-500 bg-red-500/10",
    warning: "border-amber-400 bg-amber-400/10",
    ok: "border-sky-400/70",
    neutral: "border-slate-600",
  }[tone];

  return (
    <div className="relative mx-auto aspect-square w-full max-w-md">
      {MARKS_M.map((mark) => (
        <div
          key={mark}
          className="absolute rounded-full border border-line"
          style={{ inset: `${50 - (mark / RANGE_M) * 50}%` }}
        >
          <span className="absolute -top-2 left-1/2 -translate-x-1/2 bg-panel px-1 font-mono text-[10px] text-slate-400">
            {mark} m
          </span>
        </div>
      ))}
      {ringPct !== null && (
        <div
          className={`absolute rounded-full border-2 transition-all duration-500 ${ringColor} ${tone === "critical" ? "animate-pulse" : ""}`}
          style={{ inset: `${50 - ringPct / 2}%` }}
          aria-hidden="true"
        />
      )}
      <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center">
        <div className="h-4 w-4 rounded-full border-2 border-black bg-brand shadow-lg" />
        <span className="mt-1 rounded bg-black/80 px-1 font-mono text-[10px] font-bold text-brand">Machine</span>
      </div>
    </div>
  );
}

export default function FleetRadarPage(): React.ReactElement {
  const { status, incidents } = useMachine();
  const live = status?.live ?? false;
  const distance = live ? status?.proximity_distance ?? null : null;
  const incident = incidents.find((i) => i.incident_type === "proximity" && i.status !== "resolved" && !i.event_end);
  const tone = incident ? (incident.peak_severity === "critical" ? "critical" : "warning") : live ? "ok" : "neutral";

  const reading = !live ? "No data" : distance === null ? "Nothing in range" : number(distance, 1, "m");

  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="radar"
          title="Proximity"
          subtitle="Nearest person or object reported by the proximity sensor. The sensor gives a distance only, not a direction."
        />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <Panel
            className="lg:col-span-8"
            title="Nearest object"
            icon="sensors"
            aside={
              <Badge tone={tone}>
                {incident ? `${incident.peak_severity} alert` : live ? "Clear" : "No live data"}
              </Badge>
            }
            footer={<span>Alerts come from the on-machine safety engine</span>}
          >
            <div className="grid items-center gap-6 md:grid-cols-[1fr_auto]">
              <Scope distance={distance} tone={tone} />
              <div className="text-center md:text-left">
                <div className="font-display text-xs font-bold uppercase text-slate-400">Distance</div>
                <div className="font-mono text-4xl font-extrabold text-white">{reading}</div>
                {incident && (
                  <p role="alert" className="mt-2 max-w-xs text-sm text-red-200">
                    Person or object close to the machine. Stop and check the area.
                  </p>
                )}
              </div>
            </div>
          </Panel>

          <Panel className="lg:col-span-4" title="Nearby equipment" icon="local_shipping" aside={<IllustrativeTag />}>
            <ul className="space-y-3">
              {SAMPLE_ASSETS.map((asset) => (
                <li key={asset.id} className="rounded-lg border border-line bg-well p-3">
                  <div className="flex items-center justify-between font-mono text-xs">
                    <span className="font-bold text-brand">{asset.id}</span>
                    <span className="font-bold text-slate-300">{asset.range}</span>
                  </div>
                  <div className="mt-0.5 font-display text-sm font-bold uppercase text-white">{asset.name}</div>
                  <div className="font-mono text-[11px] text-slate-400">{asset.status}</div>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-400">
              Sample list. Positions of other machines are not part of this demo.
            </p>
          </Panel>
        </div>
      </main>
    </div>
  );
}
