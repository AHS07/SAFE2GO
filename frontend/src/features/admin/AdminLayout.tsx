/** Frame for the admin (cloud) screens: navigation, cloud link state, and sign out. */
import React from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "@/features/auth/session";
import { useDemo } from "@/features/demo-controls/DemoContext";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon, { type IconName } from "@/shared/ui/Icon";

import { usePolledResource, useToken } from "./useAdminData";
import { adminApi } from "@/api/admin";

const NAV: { to: string; label: string; icon: IconName; end: boolean }[] = [
  { to: "/admin", label: "Assignments", icon: "assignment", end: true },
  { to: "/admin/fleet", label: "Operators and machines", icon: "groups", end: false },
  { to: "/admin/conflicts", label: "Sync", icon: "sync_problem", end: false },
  { to: "/admin/pipeline", label: "Data pipeline", icon: "insights", end: false },
];

function SyncState(): React.ReactElement | null {
  const demo = useDemo();
  if (!demo.available || demo.cloudReachable === null) return null;
  const queued = demo.pending?.cloud ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={demo.cloudReachable ? "ok" : "warning"}>
        <Icon name={demo.cloudReachable ? "cloud_done" : "cloud_off"} className="text-sm" />
        {demo.cloudReachable ? "Edge link up" : "Edge link down"}
      </Badge>
      <Badge tone={queued > 0 ? "warning" : "neutral"}>{queued} queued for the edge</Badge>
      <Button
        compact
        variant={demo.cloudReachable ? "secondary" : "danger"}
        disabled={demo.busy}
        onClick={() => void demo.setCloudReachable(!demo.cloudReachable)}
      >
        {demo.cloudReachable ? "Cut link (demo)" : "Restore link (demo)"}
      </Button>
    </div>
  );
}

export default function AdminLayout(): React.ReactElement {
  const { signOut } = useSession();
  const { error } = useDemo();
  const token = useToken();
  const conflicts = usePolledResource(() => adminApi.conflicts(token), [token]);
  const deadLetters = usePolledResource(() => adminApi.deadLetters(token), [token]);
  // Everything on the Sync page that needs the admin: open conflicts and dead letters.
  const unresolvedCount =
    (conflicts.data?.filter((c) => !c.resolved).length ?? 0) + (deadLetters.data?.length ?? 0);

  return (
    <div className="app-grid-bg min-h-screen">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex w-full max-w-[1500px] flex-wrap items-center justify-between gap-3 px-4 py-3 lg:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded border border-brand/60 bg-brand/10 font-display text-sm font-extrabold text-brand">
              S2G
            </div>
            <div>
              <span className="font-display text-lg font-bold uppercase leading-none tracking-wide text-white">SAFE2GO</span>
              <span className="mt-0.5 block font-mono text-[10px] uppercase tracking-wider text-slate-400">Site admin</span>
            </div>
          </div>
          <SyncState />
          <Button compact variant="ghost" onClick={signOut}>
            <Icon name="logout" className="text-sm" /> Sign out
          </Button>
        </div>
        <nav className="mx-auto flex w-full max-w-[1500px] gap-1 overflow-x-auto px-4 lg:px-6" aria-label="Admin">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex min-h-12 items-center gap-2 border-b-2 px-4 font-display text-sm font-bold uppercase tracking-wider ${
                  isActive ? "border-brand text-white" : "border-transparent text-slate-400 hover:text-slate-200"
                }`
              }
            >
              <Icon name={item.icon} className="text-base" />
              {item.label}
              {item.to === "/admin/conflicts" && unresolvedCount > 0 && (
                <span className="ml-1 rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-xs font-bold text-amber-400 border border-amber-500/40">
                  {unresolvedCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="mx-auto w-full max-w-[1500px] space-y-4 p-4 lg:p-6">
        {error !== null && <ErrorNotice error={error} />}
        <Outlet />
      </main>
    </div>
  );
}
