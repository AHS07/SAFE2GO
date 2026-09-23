/** Operator shift summary: the current shift so far, from the machine unit. */
import React from "react";
import { operatorApi } from "@/api/operator";
import { useSession } from "@/features/auth/session";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import ErrorNotice from "@/shared/components/ErrorNotice";
import { useResource } from "@/shared/hooks/useResource";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import PageHeader from "@/shared/ui/PageHeader";
import ShiftReportView from "./ShiftReportView";

export default function ShiftSummaryPage(): React.ReactElement {
  const { session } = useSession();
  const token = session?.token ?? "";
  const report = useResource(() => operatorApi.shiftSummary(token), [token]);

  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="summarize"
          title="Shift summary"
          subtitle="Your shift so far: tasks, working and idle time, safety, and training to review."
          aside={
            <Button onClick={report.reload} disabled={report.loading}>
              <Icon name="refresh" className="text-base" /> Refresh
            </Button>
          }
        />
        {report.error !== null && <ErrorNotice error={report.error} onRetry={report.reload} />}
        {report.data ? <ShiftReportView report={report.data} /> : report.loading && <p className="text-sm text-slate-400">Loading</p>}
      </main>
    </div>
  );
}
