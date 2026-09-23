import React from "react";
import ManualQuickSearch from "@/features/assistant/ManualQuickSearch";
import CoachingPanel from "@/features/coaching/CoachingPanel";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import CurrentTaskPanel from "@/features/dispatch/CurrentTaskPanel";
import ServiceNotice from "@/features/maintenance/ServiceNotice";
import { useMachine } from "@/features/machine/MachineContext";
import SafetyChecksPanel from "@/features/safety/SafetyChecksPanel";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Icon from "@/shared/ui/Icon";
import CameraView from "./CameraView";
import TelemetryPanel from "./TelemetryPanel";

function NoShift(): React.ReactElement {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-line bg-panel p-10 text-center shadow-hud">
      <Icon name="event_busy" className="text-4xl text-slate-400" />
      <p className="font-display text-xl font-bold uppercase text-slate-200">No shift assigned</p>
      <p className="text-sm text-slate-400">Your supervisor has not assigned a shift to you yet.</p>
    </div>
  );
}

export default function CockpitDashboard(): React.ReactElement {
  const { loading, error, shift, reload } = useMachine();

  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        {error !== null && <ErrorNotice error={error} onRetry={reload} />}
        {loading && !shift && <p className="text-sm text-slate-400">Loading your shift.</p>}
        {!loading && error === null && !shift && <NoShift />}

        {shift && (
          <>
            <ServiceNotice />
            <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-12">
              <div className="lg:col-span-6">
                <SafetyChecksPanel />
              </div>
              <div className="lg:col-span-6">
                <CurrentTaskPanel />
              </div>
            </div>
            <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-12">
              <div className="xl:col-span-8">
                <CameraView />
              </div>
              <div className="xl:col-span-4">
                <TelemetryPanel />
              </div>
            </div>
            <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-12">
              <div className="lg:col-span-7">
                <ManualQuickSearch />
              </div>
              <div className="lg:col-span-5">
                <CoachingPanel />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
