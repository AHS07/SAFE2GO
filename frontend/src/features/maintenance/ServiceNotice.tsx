/** Service suggestion for the operator's machine (PRD F11). Hidden while no service is due. */
import React, { useEffect } from "react";
import { operatorApi } from "@/api/operator";
import { useSession } from "@/features/auth/session";
import { useResource } from "@/shared/hooks/useResource";
import Notice from "@/shared/ui/Notice";

// Engine hours change slowly; a minute is frequent enough.
const REFRESH_MS = 60_000;

export default function ServiceNotice(): React.ReactElement | null {
  const { session } = useSession();
  const token = session?.token ?? "";
  const service = useResource(() => operatorApi.maintenance(token), [token]);
  const { reload } = service;

  useEffect(() => {
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const data = service.data;
  if (!data || data.status === "ok" || !data.message) return null;
  return (
    <Notice tone={data.status === "overdue" ? "critical" : "warning"} role="status">
      <p>
        <strong>{data.status === "overdue" ? "Service overdue." : "Service due soon."}</strong> {data.message}
      </p>
      <p className="font-mono text-xs text-slate-300">
        {data.hours_since_service.toFixed(0)} of {data.service_interval_hours.toFixed(0)} engine hours since the last service
      </p>
    </Notice>
  );
}
