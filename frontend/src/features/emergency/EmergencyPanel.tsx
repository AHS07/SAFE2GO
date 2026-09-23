/** Full-screen emergency guidance. Opens over any screen with one tap. */
import React, { useEffect, useState } from "react";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import Notice from "@/shared/ui/Notice";
import { useEmergency } from "./EmergencyContext";

export default function EmergencyPanel(): React.ReactElement | null {
  const { guide, failed, isOpen, close, retry } = useEmergency();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) setSelectedId(null);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, close]);

  if (!isOpen) return null;
  const selected = guide?.items.find((item) => item.emergency_id === selectedId) ?? null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ink" role="dialog" aria-modal="true" aria-label="Emergency guidance">
      <div className="hazard-stripe h-2" />
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="flex items-center gap-2 font-display text-3xl font-bold uppercase tracking-wide text-white">
            <Icon name="emergency" className="text-3xl text-red-500" />
            {selected ? selected.title : "Emergency guidance"}
          </h1>
          <div className="flex gap-2">
            {selected && <Button onClick={() => setSelectedId(null)}>All emergencies</Button>}
            <Button onClick={close}>Close</Button>
          </div>
        </div>

        {!guide && failed && (
          <Notice tone="critical" role="alert">
            <p>Guidance could not be loaded. Follow your site emergency plan and call the site emergency contact.</p>
            <Button onClick={retry}>
              Try again
            </Button>
          </Notice>
        )}
        {!guide && !failed && <p className="text-slate-400">Loading guidance.</p>}

        {guide && !selected && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {guide.items.map((item) => (
              <button
                key={item.emergency_id}
                type="button"
                onClick={() => setSelectedId(item.emergency_id)}
                className="tactile-btn flex min-h-24 flex-col items-start gap-1 rounded-lg border-2 border-red-500/70 bg-red-950/40 p-4 text-left hover:bg-red-950/70"
              >
                <span className="font-display text-xl font-bold uppercase text-white">{item.title}</span>
                <span className="text-sm text-slate-300">{item.summary}</span>
              </button>
            ))}
          </div>
        )}

        {selected && (
          <ol className="space-y-3">
            {selected.steps.map((step, i) => (
              <li key={i} className="flex gap-4 rounded-lg border border-line bg-panel p-4">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-600 font-display text-lg font-bold text-white">
                  {i + 1}
                </span>
                <span className="self-center text-xl leading-snug text-slate-100">{step}</span>
              </li>
            ))}
          </ol>
        )}

        {guide && <p className="text-sm text-slate-400">{guide.notice}</p>}
      </div>
    </div>
  );
}
