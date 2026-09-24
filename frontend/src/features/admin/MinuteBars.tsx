/**
 * Per-minute working and idle time for one machine, built from the Kafka archive.
 *
 * One bar per minute; full height is 60 ticks (one minute of sensor data).
 * Working sits on the baseline, idle stacks above it with a 2 px surface gap.
 * The remainder of the bar is engine off or moving without hydraulics.
 * Colors are categorical slots 1 and 2 (dark steps), validated against the
 * panel surface; identity is also carried by the legend and the tooltip.
 */
import React, { useState } from "react";
import type { RollupPoint } from "@/shared/types/api";
import { shortTime } from "@/shared/utils/format";

export const SERIES = {
  working: { label: "Working", color: "#3987e5" },
  idle: { label: "Idle", color: "#d95926" },
} as const;

const SURFACE = "#14171c"; // --color-panel, the chart surface
const TICKS_PER_MINUTE = 60;
const HEIGHT = 72;
const SLOT = 10; // horizontal units per minute
const GAP = 2;

export function Legend(): React.ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-slate-300" aria-label="Legend">
      {Object.values(SERIES).map((s) => (
        <span key={s.label} className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-2.5 rounded-sm border border-line" />
        Engine off or travelling
      </span>
    </div>
  );
}

export default function MinuteBars({ points, title }: { points: RollupPoint[]; title: string }): React.ReactElement {
  const [hover, setHover] = useState<number | null>(null);
  const width = Math.max(points.length, 1) * SLOT;
  const scale = (ticks: number) => (Math.min(ticks, TICKS_PER_MINUTE) / TICKS_PER_MINUTE) * HEIGHT;
  const active = hover !== null ? { index: hover, point: points[hover] } : null;

  return (
    <figure className="relative space-y-1">
      <svg
        viewBox={`0 0 ${width} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="block h-[72px] w-full"
        role="img"
        aria-label={`${title}: working and idle seconds per minute`}
        onMouseLeave={() => setHover(null)}
      >
        <line x1={0} x2={width} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} stroke="#2a303c" strokeWidth={1} />
        {points.map((p, i) => {
          const x = i * SLOT + GAP / 2;
          const w = SLOT - GAP;
          const working = scale(p.working_ticks);
          const idle = scale(p.idle_ticks);
          const idleTop = HEIGHT - working - (working > 0 && idle > 0 ? GAP : 0) - idle;
          return (
            <g key={p.bucket_start} opacity={hover === null || hover === i ? 1 : 0.45}>
              {working > 0 && (
                <rect x={x} y={HEIGHT - working} width={w} height={working} fill={SERIES.working.color} rx={1} />
              )}
              {idle > 0 && <rect x={x} y={idleTop} width={w} height={idle} fill={SERIES.idle.color} rx={1} />}
              {/* Hit target spans the full column, larger than the marks. */}
              <rect
                x={i * SLOT}
                y={0}
                width={SLOT}
                height={HEIGHT}
                fill={SURFACE}
                fillOpacity={0}
                onMouseEnter={() => setHover(i)}
              />
            </g>
          );
        })}
      </svg>
      <figcaption className="flex justify-between font-mono text-[10px] text-slate-400">
        <span>{points.length > 0 ? shortTime(points[0].bucket_start) : ""}</span>
        <span>{points.length > 0 ? shortTime(points[points.length - 1].bucket_start) : ""}</span>
      </figcaption>
      {active && (
        <div
          role="tooltip"
          className="pointer-events-none absolute top-0 z-10 rounded border border-line bg-raised px-2 py-1 text-xs text-slate-200 shadow"
          style={{
            left: `${((active.index + 0.5) / points.length) * 100}%`,
            transform: `translateX(${active.index > points.length / 2 ? "-105%" : "5%"})`,
          }}
        >
          <p className="font-mono text-slate-400">{shortTime(active.point.bucket_start)}</p>
          <p>
            <span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: SERIES.working.color }} />
            Working {active.point.working_ticks} s
          </p>
          <p>
            <span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: SERIES.idle.color }} />
            Idle {active.point.idle_ticks} s
          </p>
          <p className="text-slate-400">
            {active.point.ticks} ticks · {Math.round(active.point.avg_rpm)} rpm · {active.point.load_cycles} cycles
          </p>
        </div>
      )}
    </figure>
  );
}
