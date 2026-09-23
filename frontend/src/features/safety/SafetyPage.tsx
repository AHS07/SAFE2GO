import React from "react";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import PageHeader from "@/shared/ui/PageHeader";
import IncidentLog from "./IncidentLog";
import ReportIncidentForm from "./ReportIncidentForm";
import SafetyChecksPanel from "./SafetyChecksPanel";

export default function SafetyPage(): React.ReactElement {
  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="security"
          title="Safety"
          subtitle="Live safety checks, open alerts, and the incident log for this shift. Critical alerts need acknowledgement while the machine is stationary."
        />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <SafetyChecksPanel />
          </div>
          <div className="space-y-4 lg:col-span-5">
            <IncidentLog />
            <ReportIncidentForm />
          </div>
        </div>
      </main>
    </div>
  );
}
