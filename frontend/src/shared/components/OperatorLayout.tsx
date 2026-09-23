/** Frame for every signed-in operator screen. The emergency button is always one tap away. */
import React from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "@/features/auth/session";
import { useDemo } from "@/features/demo-controls/DemoContext";
import { useEmergency } from "@/features/emergency/EmergencyContext";
import { useMachine } from "@/features/machine/MachineContext";
import RecommendationToast from "@/features/machine/RecommendationToast";
import Icon, { type IconName } from "@/shared/ui/Icon";
import { clockTime, label } from "@/shared/utils/format";

const NAV_TABS: { to: string; label: string; icon: IconName }[] = [
  { to: "/", label: "Cockpit", icon: "dashboard" },
  { to: "/safety", label: "Safety", icon: "verified_user" },
  { to: "/dispatch", label: "Tasks", icon: "assignment" },
  { to: "/radar", label: "Proximity", icon: "radar" },
  { to: "/manuals", label: "Manuals", icon: "menu_book" },
  { to: "/training", label: "Training", icon: "school" },
  { to: "/summary", label: "Summary", icon: "summarize" },
];

function Logo(): React.ReactElement {
  return (
    <div className="flex items-center gap-3 border-r border-line pr-4">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-brand/60 bg-brand/10 font-display text-sm font-extrabold text-brand">
        S2G
      </div>
      <div className="flex flex-col">
        <span className="font-display text-lg font-bold uppercase leading-none tracking-wide text-white">SAFE2GO</span>
        <span className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">Operator assistant</span>
      </div>
    </div>
  );
}

function MachineBadge(): React.ReactElement | null {
  const { shift, incidents } = useMachine();
  if (!shift) return null;
  const alarm = incidents.some((i) => i.status !== "resolved");
  return (
    <div className="hidden items-center gap-2.5 rounded-md border border-line bg-well px-3 py-1.5 md:flex">
      <span className={`h-2 w-2 rounded-full pulse-dot ${alarm ? "bg-red-500" : "bg-brand"}`} />
      <div className="flex flex-col">
        <span className="font-display text-xs font-bold uppercase leading-none tracking-wider text-slate-200">
          {shift.machine_model}
        </span>
        <span className="mt-0.5 font-mono text-[10px] text-slate-400">
          {label(shift.machine_type)} · bucket {shift.bucket_capacity} m³
        </span>
      </div>
    </div>
  );
}

function LinkStatus(): React.ReactElement {
  const { connected } = useMachine();
  const { cloudReachable } = useDemo();
  const { session } = useSession();
  return (
    <div className="hidden items-center gap-3 rounded-md border border-line bg-well px-3 py-1.5 font-mono text-xs uppercase xl:flex">
      <span className={connected ? "text-emerald-400" : "text-red-400"}>
        <Icon name={connected ? "sensors" : "sensors_off"} className="mr-1 text-sm" />
        {connected ? "Machine link" : "Machine link lost"}
      </span>
      {cloudReachable !== null && (
        <span className={cloudReachable ? "text-emerald-400" : "text-amber-300"}>
          <Icon name={cloudReachable ? "cloud_done" : "cloud_off"} className="mr-1 text-sm" />
          {cloudReachable ? "Cloud online" : "Cloud offline"}
        </span>
      )}
      {session?.offline && (
        <span className="text-amber-300" title="Signed in with the shift PIN while the cloud was offline">
          <Icon name="key" className="mr-1 text-sm" />
          PIN sign-in
        </span>
      )}
    </div>
  );
}

function SimClock(): React.ReactElement | null {
  const { available, sim } = useDemo();
  const { status } = useMachine();
  if (!available || !sim) return null;
  const time = status?.live && status.timestamp ? status.timestamp : sim.sim_time;
  const slowed = sim.effective_speed < sim.selected_speed;
  return (
    <div className="flex items-center gap-2 rounded-md border border-line bg-well px-3 py-1.5" title="Simulation clock">
      <Icon name="schedule" className="text-sm text-slate-400" />
      <div className="flex flex-col text-right">
        <span className="font-mono text-[9px] uppercase leading-none tracking-wider text-slate-400">
          Sim {sim.effective_speed}x{slowed ? " (slowed)" : ""}
        </span>
        <span className="font-mono text-xs font-bold text-brand">{clockTime(time)}</span>
      </div>
    </div>
  );
}

export default function OperatorLayout(): React.ReactElement {
  const { signOut } = useSession();
  const { open: openEmergency } = useEmergency();
  const { shift } = useMachine();

  return (
    <div className="app-grid-bg flex min-h-screen flex-col font-sans text-slate-100">
      <header className="sticky top-0 z-40 border-b border-line bg-panel-head shadow-md">
        <div className="flex h-16 items-center justify-between gap-3 px-4 lg:px-6">
          <div className="flex items-center gap-4">
            <Logo />
            <MachineBadge />
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <LinkStatus />
            <SimClock />
            {shift && (
              <div className="hidden items-center gap-2.5 border-l border-line pl-3 sm:flex">
                <div className="flex h-8 w-8 items-center justify-center rounded border border-line bg-well font-display text-xs font-bold text-brand">
                  {shift.operator_name.slice(0, 2).toUpperCase()}
                </div>
                <span className="text-xs font-bold text-slate-200">{shift.operator_name}</span>
              </div>
            )}
            <button
              type="button"
              onClick={openEmergency}
              className="hazard-stripe tactile-btn flex min-h-12 items-center gap-1.5 rounded border border-red-500 px-4 font-display text-sm font-extrabold uppercase tracking-wider text-white shadow-md hover:brightness-110"
            >
              <Icon name="emergency" className="text-lg" />
              <span className="drop-shadow">Emergency</span>
            </button>
            <button
              type="button"
              onClick={signOut}
              aria-label="Sign out"
              title="Sign out"
              className="tactile-btn flex min-h-12 min-w-12 items-center justify-center rounded text-slate-400 hover:bg-well hover:text-white"
            >
              <Icon name="logout" className="text-lg" />
            </button>
          </div>
        </div>

        <nav className="flex items-center gap-1 overflow-x-auto border-t border-line bg-panel px-4 font-display text-xs font-bold uppercase tracking-wider lg:px-6">
          {NAV_TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.to === "/"}
              className={({ isActive }) =>
                `flex min-h-12 items-center gap-2 border-b-2 px-3.5 transition-colors ${
                  isActive
                    ? "border-brand bg-brand/5 text-brand"
                    : "border-transparent text-slate-400 hover:border-slate-600 hover:text-slate-200"
                }`
              }
            >
              <Icon name={tab.icon} className="text-base" />
              <span>{tab.label}</span>
            </NavLink>
          ))}
        </nav>
      </header>

      <Outlet />
      <RecommendationToast />

      <footer className="mt-auto border-t border-line bg-panel-head px-4 py-3 text-xs text-slate-400 lg:px-6">
        <div className="mx-auto flex max-w-[1780px] flex-col items-center justify-between gap-2 sm:flex-row">
          <span className="font-display font-bold uppercase tracking-wider text-slate-300">SAFE2GO operator assistant</span>
          <span className="font-mono text-[11px]">All machine data in this demo is synthetic.</span>
        </div>
      </footer>
    </div>
  );
}
