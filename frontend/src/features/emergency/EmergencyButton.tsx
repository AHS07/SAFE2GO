import React from "react";
import Icon from "@/shared/ui/Icon";
import { useEmergency } from "./EmergencyContext";

export default function EmergencyButton(): React.ReactElement {
  const { open } = useEmergency();
  return (
    <button
      type="button"
      onClick={open}
      className="hazard-stripe tactile-btn flex min-h-12 items-center gap-1.5 rounded border border-red-500 px-4 font-display text-sm font-extrabold uppercase tracking-wider text-white shadow-md hover:brightness-110"
    >
      <Icon name="emergency" className="text-lg" />
      <span className="drop-shadow">Emergency</span>
    </button>
  );
}
