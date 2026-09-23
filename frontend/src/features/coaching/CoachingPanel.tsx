/**
 * Behavior coaching (prd.md F3.2): patterns the edge detectors found this
 * shift, in plain language, with the linked training module. Coaching,
 * never scoring.
 */
import React from "react";
import { Link } from "react-router-dom";
import { useMachine } from "@/features/machine/MachineContext";
import Badge from "@/shared/ui/Badge";
import Icon from "@/shared/ui/Icon";
import Panel from "@/shared/ui/Panel";
import { label, shortTime } from "@/shared/utils/format";

export default function CoachingPanel(): React.ReactElement {
  const { coaching } = useMachine();
  return (
    <Panel
      title="Coaching"
      icon="insights"
      aside={<Badge>{coaching.length} this shift</Badge>}
      footer={<span>Compared with your own typical range (median and MAD)</span>}
    >
      {coaching.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Icon name="thumb_up" className="text-emerald-400" />
          No coaching notes this shift.
        </div>
      ) : (
        <ul className="space-y-2.5">
          {coaching.map((note) => (
            <li key={note.event_id} className="rounded-lg border border-line bg-well p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-display text-sm font-bold uppercase text-slate-100">{label(note.event_type)}</span>
                <span className="font-mono text-[11px] text-slate-400">{shortTime(note.end)}</span>
              </div>
              <p className="mt-1 text-sm text-slate-300">{note.message}</p>
              {note.module_id && (
                <Link
                  to="/training"
                  className="mt-2 inline-flex min-h-12 items-center gap-1.5 rounded border border-brand/40 px-3 font-display text-xs font-bold uppercase text-brand hover:bg-brand/10"
                >
                  <Icon name="school" className="text-sm" /> {note.module_title}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
