import React from "react";

/** Marks a visual that is a sample, not live data from the machine. */
export default function IllustrativeTag(): React.ReactElement {
  return (
    <span
      title="Sample visual. This value does not come from the machine."
      className="inline-flex items-center rounded border border-dashed border-slate-500 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-slate-400"
    >
      Illustrative
    </span>
  );
}
