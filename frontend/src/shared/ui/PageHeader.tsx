import React from "react";
import Icon, { type IconName } from "./Icon";

export default function PageHeader({
  icon,
  title,
  subtitle,
  aside,
}: {
  icon: IconName;
  title: string;
  subtitle: string;
  aside?: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-line bg-panel p-4 shadow-hud">
      <div>
        <h1 className="flex items-center gap-2 font-display text-2xl font-bold uppercase tracking-wider text-white">
          <Icon name={icon} className="text-2xl text-brand" />
          {title}
        </h1>
        <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
      </div>
      {aside && <div className="flex flex-wrap items-center gap-3">{aside}</div>}
    </div>
  );
}
