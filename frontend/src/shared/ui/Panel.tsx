import React from "react";
import Icon, { type IconName } from "./Icon";

interface Props {
  title: string;
  icon: IconName;
  aside?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

/** Card with a dark title bar, used for every dashboard section. */
export default function Panel({ title, icon, aside, footer, children, className = "" }: Props): React.ReactElement {
  return (
    <section className={`flex flex-col overflow-hidden rounded-lg border border-line bg-panel shadow-hud ${className}`}>
      <header className="flex items-center justify-between gap-3 border-b border-line bg-panel-head px-4 py-2.5">
        <h2 className="flex items-center gap-2 font-display text-sm font-bold uppercase tracking-wider text-white sm:text-base">
          <Icon name={icon} className="text-lg text-brand" />
          <span>{title}</span>
        </h2>
        {aside && <div className="flex shrink-0 items-center gap-2">{aside}</div>}
      </header>
      <div className="flex-1 p-4">{children}</div>
      {footer && (
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-well px-4 py-2.5 font-mono text-[11px] text-slate-400">
          {footer}
        </footer>
      )}
    </section>
  );
}
