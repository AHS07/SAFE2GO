import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { useSession } from "@/features/auth/session";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import { useMachine } from "@/features/machine/MachineContext";
import ErrorNotice from "@/shared/components/ErrorNotice";
import { useResource } from "@/shared/hooks/useResource";
import Badge from "@/shared/ui/Badge";
import Icon from "@/shared/ui/Icon";
import Notice from "@/shared/ui/Notice";
import PageHeader from "@/shared/ui/PageHeader";
import type { ModuleSummary } from "@/shared/types/api";
import { trainingApi } from "./api";
import StatusBadge from "./StatusBadge";

// The backend decides whether the machine is parked; refresh as a fallback in case a push is missed.
const REFRESH_MS = 10_000;

function ModuleCard({ module, parked }: { module: ModuleSummary; parked: boolean }): React.ReactElement {
  const body = (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-display text-lg font-bold uppercase text-white">{module.title}</span>
        <div className="flex items-center gap-2">
          {module.recommended && <Badge tone="brand">Recommended</Badge>}
          <StatusBadge status={module.status} />
        </div>
      </div>
      <span className="flex items-center gap-1.5 font-mono text-xs text-slate-400">
        <Icon name={parked ? "timer" : "lock"} className="text-sm" />
        {module.duration_min} min with quiz
      </span>
    </>
  );
  const base = "flex min-h-12 flex-col gap-2 rounded-lg border p-4";
  if (!parked) {
    return (
      <div className={`${base} cursor-not-allowed border-line bg-well opacity-60`} aria-disabled="true">
        {body}
      </div>
    );
  }
  return (
    <Link to={`/training/${module.module_id}`} className={`${base} border-line bg-panel shadow-hud hover:border-brand/60`}>
      {body}
    </Link>
  );
}

export default function TrainingListPage(): React.ReactElement {
  const { session } = useSession();
  const { status } = useMachine();
  const token = session?.token ?? "";
  const parkedNow = status?.parked ?? false;
  const modules = useResource(() => trainingApi.listModules(token), [token, parkedNow]);
  const { reload } = modules;

  useEffect(() => {
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const list = modules.data;
  const recommended = list?.modules.filter((m) => m.recommended) ?? [];
  const others = list?.modules.filter((m) => !m.recommended) ?? [];

  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1100px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="school"
          title="Training"
          subtitle="Short modules with a quiz. Modules open while the machine is parked."
          aside={list ? <Badge tone="neutral">Pass mark {list.pass_pct}%</Badge> : undefined}
        />

        {modules.error !== null && !list && <ErrorNotice error={modules.error} onRetry={reload} />}
        {modules.loading && !list && <p className="text-sm text-slate-400">Loading modules.</p>}

        {list && !list.parked && (
          <Notice tone="warning" role="status">
            <p>Training opens when the machine is parked. Stop and set the park brake.</p>
          </Notice>
        )}

        {list && recommended.length > 0 && (
          <section className="space-y-2.5">
            <h2 className="font-display text-sm font-bold uppercase tracking-wider text-brand">Recommended for this shift</h2>
            {recommended.map((m) => (
              <ModuleCard key={m.module_id} module={m} parked={list.parked} />
            ))}
          </section>
        )}

        {list && list.modules.length === 0 && (
          <p className="rounded-lg border border-line bg-panel p-4 text-sm text-slate-400">
            No training modules on this machine yet. They arrive with the next sync from the cloud.
          </p>
        )}

        {list && others.length > 0 && (
          <section className="space-y-2.5">
            <h2 className="font-display text-sm font-bold uppercase tracking-wider text-slate-400">
              {recommended.length > 0 ? "All modules" : "Modules"}
            </h2>
            {others.map((m) => (
              <ModuleCard key={m.module_id} module={m} parked={list.parked} />
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
