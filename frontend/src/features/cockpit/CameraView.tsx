/**
 * Camera view. The machine has no camera feed in this demo, so the image
 * and presets are illustrative. The proximity overlay is live: it appears
 * only when the backend safety engine has an open proximity incident.
 */
import React, { useRef, useState } from "react";
import { useMachine } from "@/features/machine/MachineContext";
import IllustrativeTag from "@/shared/ui/IllustrativeTag";
import Icon from "@/shared/ui/Icon";
import Panel from "@/shared/ui/Panel";
import { animateFlash, animateReticleLock } from "@/shared/utils/animations";
import { number } from "@/shared/utils/format";
import cameraScene from "./camera-scene.svg";

const PRESETS = [
  { id: "rear", label: "Rear" },
  { id: "left", label: "Left side" },
  { id: "bucket", label: "Bucket" },
  { id: "overhead", label: "Overhead" },
];

export default function CameraView(): React.ReactElement {
  const { incidents, status } = useMachine();
  const [preset, setPreset] = useState("rear");
  const flashRef = useRef<HTMLDivElement>(null);
  const reticleRef = useRef<HTMLDivElement>(null);

  const proximity = incidents.find((i) => i.incident_type === "proximity" && i.status !== "resolved" && !i.event_end);
  const distance = status?.live ? status.proximity_distance : null;

  const choosePreset = (id: string) => {
    setPreset(id);
    animateReticleLock(reticleRef.current);
  };

  return (
    <Panel
      title="Camera view"
      icon="videocam"
      aside={<IllustrativeTag />}
      footer={
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 font-display text-[11px] font-bold uppercase text-slate-400">View:</span>
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => choosePreset(p.id)}
              aria-pressed={preset === p.id}
              className={`tactile-btn min-h-12 rounded border px-3 font-display text-xs font-bold uppercase ${
                preset === p.id
                  ? "border-brand/60 bg-panel-head text-brand"
                  : "border-line bg-raised text-slate-300 hover:bg-raised-hover"
              }`}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => animateFlash(flashRef.current)}
            className="tactile-btn ml-auto min-h-12 rounded border border-line bg-raised px-3 font-display text-xs font-bold uppercase text-slate-300 hover:bg-raised-hover"
          >
            <Icon name="photo_camera" className="mr-1 text-sm" />
            Snapshot
          </button>
        </div>
      }
    >
      <div className="relative -m-4 aspect-[16/9] select-none overflow-hidden bg-[#07090c] sm:aspect-auto sm:h-[400px]">
        <img
          alt="Illustrative view from the cab of an excavator working at a quarry face"
          className="h-full w-full object-cover brightness-[0.85]"
          src={cameraScene}
        />
        <div ref={flashRef} className="pointer-events-none absolute inset-0" />

        {proximity && (
          <div className="pointer-events-none absolute inset-x-8 bottom-12 top-12 flex items-center justify-center rounded-lg border-2 border-red-500/80 bg-red-500/10">
            <div
              role="alert"
              className="flex items-center gap-2 rounded border border-red-500 bg-red-950/90 px-4 py-2 font-display text-sm font-bold uppercase tracking-wider text-red-100 shadow-2xl"
            >
              <Icon name="warning" className="text-red-400" />
              Person or object at {number(distance, 1, "m")}. Stop and check.
            </div>
          </div>
        )}

        <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
          <div className="hud-reticle self-start rounded-lg p-2.5 font-mono text-[11px] text-slate-200">
            <span className="font-bold uppercase text-brand">{PRESETS.find((p) => p.id === preset)?.label} camera</span>
            <div className="text-slate-400">Sample image, not a live feed</div>
          </div>
          <div ref={reticleRef} className="self-center">
            <div className="relative flex h-24 w-24 items-center justify-center rounded-full border border-brand/50">
              <div className="h-16 w-16 animate-spin rounded-full border border-dashed border-brand/70" style={{ animationDuration: "24s" }} />
              <div className="absolute h-px w-full bg-brand/60" />
              <div className="absolute h-full w-px bg-brand/60" />
            </div>
          </div>
          <div />
        </div>
      </div>
    </Panel>
  );
}
