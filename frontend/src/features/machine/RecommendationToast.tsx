import React from "react";
import { Link } from "react-router-dom";
import Icon from "@/shared/ui/Icon";
import { useMachine } from "./MachineContext";

/** Shows a training recommendation as soon as the backend records one. */
export default function RecommendationToast(): React.ReactElement | null {
  const { notice, dismissNotice } = useMachine();
  if (!notice) return null;
  return (
    <div
      role="status"
      className="fixed bottom-6 right-4 z-30 flex max-w-sm items-start gap-3 rounded-lg border border-brand/50 bg-panel p-4 shadow-hud"
    >
      <Icon name="school" className="text-2xl text-brand" />
      <div className="flex-1 space-y-2">
        <p className="font-display text-sm font-bold uppercase tracking-wide text-brand">Training recommended</p>
        <p className="text-sm text-slate-200">{notice.module_title}. Open it the next time the machine is parked.</p>
        <div className="flex gap-2">
          <Link
            to="/training"
            onClick={dismissNotice}
            className="tactile-btn inline-flex min-h-12 items-center rounded border border-brand-dark bg-brand px-3 font-display text-xs font-extrabold uppercase text-black"
          >
            View training
          </Link>
          <button
            type="button"
            onClick={dismissNotice}
            className="tactile-btn min-h-12 rounded px-3 font-display text-xs font-bold uppercase text-slate-300 hover:bg-raised"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
