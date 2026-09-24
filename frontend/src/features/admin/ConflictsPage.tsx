/**
 * Sync: conflicts (cloud changes rolled back because the machine had already
 * started the task) and dead letters (messages that kept failing and were set
 * aside so later ones could continue).
 */
import React, { useState } from "react";
import { adminApi } from "@/api/admin";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import type { DeadLetter, SyncConflict } from "@/shared/types/api";
import { clockTime, label, shortId } from "@/shared/utils/format";
import { usePolledResource, useToken } from "./useAdminData";

const EXPLAIN: Record<SyncConflict["conflict_type"], string> = {
  reassign_after_start: "The task was reassigned, but the operator had already started it. The reassignment was undone.",
  cancel_after_start: "The task was cancelled, but the operator had already started it. The cancellation was undone.",
};

const DIRECTION: Record<DeadLetter["direction"], string> = {
  cloud_to_edge: "Cloud to machine",
  edge_to_cloud: "Machine to cloud",
};

function DeadLetters({ token }: { token: string }): React.ReactElement {
  const letters = usePolledResource(() => adminApi.deadLetters(token), [token]);
  const [error, setError] = useState<unknown>(null);

  const retry = async (messageId: string) => {
    setError(null);
    try {
      await adminApi.retryDeadLetter(token, messageId);
      letters.reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <Panel title="Dead letters" icon="report">
      <div className="space-y-3">
        <p className="text-sm text-slate-400">
          Messages that kept failing for the whole retry window (one hour by default) were set aside so later ones could continue. Retry sends one again, first in its
          direction.
        </p>
        {error !== null && <ErrorNotice error={error} />}
        {letters.error ? (
          <ErrorNotice error={letters.error} onRetry={letters.reload} />
        ) : !letters.data ? (
          <p className="text-sm text-slate-400">Loading</p>
        ) : letters.data.length === 0 ? (
          <p className="text-sm text-slate-400">No dead letters.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {letters.data.map((d) => (
              <li key={d.message_id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="space-y-1">
                  <p className="text-sm text-slate-100">
                    {label(d.message_type)} · {DIRECTION[d.direction]}
                    {d.entity_id ? ` · ${shortId(d.entity_id)}` : ""}
                  </p>
                  <p className="font-mono text-xs text-slate-400">
                    {d.attempts} attempts · set aside {clockTime(d.dead_at)} · {d.last_error ?? "no error recorded"}
                  </p>
                </div>
                <Button compact onClick={() => void retry(d.message_id)}>
                  Retry
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

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
        title="Sync"
        subtitle="The machine owns a task once it starts. Changes made in the cloud after that are rolled back and listed here, with any messages that could not be delivered."
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
      <DeadLetters token={token} />
    </div>
  );
}
