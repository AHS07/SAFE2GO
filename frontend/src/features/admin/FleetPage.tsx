/** Operators with qualifications and logins, and machines with status. */
import React, { useState } from "react";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge, { type Tone } from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";
import type { OperatorAccount, ServiceStatus } from "@/shared/types/api";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import { label, number } from "@/shared/utils/format";
import { AccountForm } from "./forms";
import { useReference } from "./useAdminData";

const CELL = "border-b border-line px-3 py-2 text-left";

const SERVICE_TONE: Record<ServiceStatus, Tone> = { ok: "neutral", due_soon: "warning", overdue: "critical" };
const SERVICE_LABEL: Record<ServiceStatus, string> = { ok: "OK", due_soon: "Due soon", overdue: "Overdue" };

function createdMessage(account: OperatorAccount): string {
  const shifts =
    account.credentials_issued === 0
      ? "They have no current or upcoming shift yet; new shifts get an offline sign-in when created."
      : `Offline sign-in issued for ${account.credentials_issued} current or upcoming shift${account.credentials_issued === 1 ? "" : "s"}.`;
  return `Login created: ${account.username}. ${shifts}`;
}

export default function FleetPage(): React.ReactElement {
  const reference = useReference();
  const [openFor, setOpenFor] = useState<string | null>(null);
  const [created, setCreated] = useState<OperatorAccount | null>(null);
  if (reference.error) return <ErrorNotice error={reference.error} onRetry={reference.reload} />;
  if (!reference.data) return <p className="text-sm text-slate-400">Loading</p>;
  const { operators, machines } = reference.data;

  return (
    <div className="space-y-4">
      <PageHeader icon="groups" title="Operators and machines" subtitle="Qualifications and machine status used by assignment checks, operator logins, and service suggestions from engine hours." />
      {created && (
        <Notice tone="info" role="status">
          <p>{createdMessage(created)}</p>
        </Notice>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={`Operators (${operators.length})`} icon="badge">
          <table className="w-full text-sm">
            <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className={CELL}>Name</th>
                <th className={CELL}>Qualifications</th>
                <th className={CELL}>Login</th>
              </tr>
            </thead>
            <tbody>
              {operators.map((o) => (
                <React.Fragment key={o.operator_id}>
                  <tr>
                    <td className={CELL}>{o.operator_name}</td>
                    <td className={CELL}>
                      <div className="flex flex-wrap gap-1">
                        {o.qualifications.map((q) => (
                          <Badge key={q.machine_type} tone="info">
                            {label(q.machine_type)} {q.skill_level}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className={CELL}>
                      {o.username !== null ? (
                        <span className="font-mono text-xs text-slate-300">{o.username}</span>
                      ) : (
                        <Button
                          compact
                          aria-expanded={openFor === o.operator_id}
                          onClick={() => setOpenFor(openFor === o.operator_id ? null : o.operator_id)}
                        >
                          {openFor === o.operator_id ? "Cancel" : "Create login"}
                        </Button>
                      )}
                    </td>
                  </tr>
                  {openFor === o.operator_id && o.username === null && (
                    <tr>
                      <td className={CELL} colSpan={3}>
                        <AccountForm
                          operator={o}
                          onCreated={(account) => {
                            setCreated(account);
                            setOpenFor(null);
                            reference.reload();
                          }}
                        />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title={`Machines (${machines.length})`} icon="construction">
          <table className="w-full text-sm">
            <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className={CELL}>Model</th>
                <th className={CELL}>Type</th>
                <th className={CELL}>Bucket</th>
                <th className={CELL}>Age</th>
                <th className={CELL}>Status</th>
                <th className={CELL}>Service</th>
              </tr>
            </thead>
            <tbody>
              {machines.map((m) => (
                <tr key={m.machine_id}>
                  <td className={CELL}>{m.machine_model}</td>
                  <td className={CELL}>{label(m.machine_type)}</td>
                  <td className={CELL}>{number(m.bucket_capacity, 1, "m³")}</td>
                  <td className={CELL}>{m.machine_age} y</td>
                  <td className={CELL}>
                    <Badge tone={m.machine_status === "available" ? "ok" : "warning"}>{label(m.machine_status)}</Badge>
                  </td>
                  <td className={CELL} title={m.service.message ?? undefined}>
                    <Badge tone={SERVICE_TONE[m.service.status]}>{SERVICE_LABEL[m.service.status]}</Badge>
                    <span className="ml-2 font-mono text-xs text-slate-400">
                      {m.service.hours_since_service.toFixed(0)} / {m.service.service_interval_hours.toFixed(0)} h
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  );
}
