import React from "react";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import PageHeader from "@/shared/ui/PageHeader";
import CurrentTaskPanel from "./CurrentTaskPanel";
import TaskQueuePanel from "./TaskQueuePanel";

export default function DispatchPage(): React.ReactElement {
  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="assignment"
          title="Tasks"
          subtitle="Your assigned work for this shift, live progress from the machine, and one planning estimate per task."
        />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <CurrentTaskPanel />
          </div>
          <div className="lg:col-span-5">
            <TaskQueuePanel />
          </div>
        </div>
      </main>
    </div>
  );
}
