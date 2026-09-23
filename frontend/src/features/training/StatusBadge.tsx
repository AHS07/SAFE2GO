import React from "react";
import Badge, { type Tone } from "@/shared/ui/Badge";
import type { TrainingStatus } from "@/shared/types/api";

const LABELS: Record<TrainingStatus, { text: string; tone: Tone }> = {
  not_started: { text: "Not started", tone: "neutral" },
  passed: { text: "Passed", tone: "ok" },
  needs_retry: { text: "Retry quiz", tone: "warning" },
};

export default function StatusBadge({ status }: { status: TrainingStatus }): React.ReactElement {
  const { text, tone } = LABELS[status];
  return <Badge tone={tone}>{text}</Badge>;
}
