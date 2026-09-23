/** Sync conflicts: cloud changes rolled back because the machine had already started the task. */
import React, { useState } from "react";
import { adminApi } from "@/api/admin";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import type { SyncConflict } from "@/shared/types/api";
import { clockTime, shortId } from "@/shared/utils/format";
import { usePolledResource, useToken } from "./useAdminData";

const EXPLAIN: Record<SyncConflict["conflict_type"], string> = {
  reassign_after_start: "The task was reassigned, but the operator had already started it. The reassignment was undone.",
  cancel_after_start: "The task was cancelled, but the operator had already started it. The cancellation was undone.",
};

export default function ConflictsPage(): React.ReactElement {
  const token = useToken();
  const conflicts = usePolledResource(() => adminApi.conflicts(token), [token]);
  const [error, setError] = useState<unknown>(null);

  const resolve = async (conflictId: string) => {
    setError(null);
    try {
      await adminApi.resolveConflict(token, conflictId);
      conflicts.reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        icon="sync_problem"
        title="Sync conflicts"
        subtitle="The machine owns a task once it starts. Changes made in the cloud after that are rolled back and listed here."
      />
      {error !== null && <ErrorNotice error={error} />}
      <Panel title="Conflicts" icon="sync_problem">
        {conflicts.error ? (
          <ErrorNotice error={conflicts.error} onRetry={conflicts.reload} />
        ) : !conflicts.data ? (
          <p className="text-sm text-slate-400">Loading</p>
        ) : conflicts.data.length === 0 ? (
          <p className="text-sm text-slate-400">No conflicts.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {conflicts.data.map((c) => (
              <li key={c.conflict_id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="space-y-1">
                  <p className="text-sm text-slate-100">{EXPLAIN[c.conflict_type]}</p>
                  <p className="font-mono text-xs text-slate-400">
                    Task {shortId(c.task_id)} · started on the machine {clockTime(c.edge_actual_start)} · changed in the cloud{" "}
                    {clockTime(c.cloud_changed_at)}
                  </p>
                </div>
                {c.resolved ? (
                  <Badge tone="ok">Reviewed</Badge>
                ) : (
                  <Button compact onClick={() => void resolve(c.conflict_id)}>
                    Mark reviewed
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
