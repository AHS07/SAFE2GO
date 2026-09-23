import React from "react";
import { ICON_CODE_POINTS, type IconName } from "./iconCodepoints";

export type { IconName };

/**
 * Material Symbols icon from the bundled subset font. Decorative: hidden
 * from screen readers. A name missing from the subset is a type error; run
 * scripts/subset_icons.py after using a new icon.
 */
export default function Icon({ name, className = "" }: { name: IconName; className?: string }): React.ReactElement {
  return (
    <span className={`material-symbols-outlined ${className}`} aria-hidden="true">
      {String.fromCodePoint(ICON_CODE_POINTS[name])}
    </span>
  );
}
