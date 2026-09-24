/**
 * Data pipeline: live telemetry from the machines' edge spool, through Kafka,
 * into cloud fleet analytics. Safety runs on the machine and never waits for
 * any of this; the page shows how far behind the analytics copy is.
 */
import React from "react";
import { adminApi } from "@/api/admin";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge, { type Tone } from "@/shared/ui/Badge";
import Notice from "@/shared/ui/Notice";
import PageHeader from "@/shared/ui/PageHeader";
import Panel from "@/shared/ui/Panel";
import type { ConsumerState, ForwarderState, MachineRollup, PipelineStatus } from "@/shared/types/api";
import { clockTime, label, number } from "@/shared/utils/format";
import MinuteBars, { Legend } from "./MinuteBars";
import type { Reference } from "./useAdminData";
import { usePolledResource, useReference, useToken } from "./useAdminData";

const ROLLUP_MINUTES = 60;
const CELL = "border-b border-line px-3 py-2 text-left";

const FORWARDER: Record<ForwarderState, { tone: Tone; text: string }> = {
  disabled: { tone: "neutral", text: "Off" },
  stopped: { tone: "neutral", text: "Stopped" },
  link_down: { tone: "warning", text: "Link down, spooling" },
  connecting: { tone: "info", text: "Connecting" },
  broker_down: { tone: "critical", text: "Kafka unreachable, spooling" },
  draining: { tone: "info", text: "Sending backlog" },
  idle: { tone: "ok", text: "Up to date" },
};

const CONSUMER: Record<ConsumerState, { tone: Tone; text: string }> = {
  stopped: { tone: "neutral", text: "Stopped" },
  connecting: { tone: "info", text: "Connecting" },
  broker_down: { tone: "critical", text: "Kafka unreachable" },
  running: { tone: "ok", text: "Running" },
};

function seconds(value: number | null): string {
  if (value === null) return "None";
  if (value < 60) return `${Math.round(value)} s`;
  if (value < 3600) return `${Math.floor(value / 60)} min ${Math.round(value % 60)} s`;
  return `${Math.floor(value / 3600)} h ${Math.round((value % 3600) / 60)} min`;
}

function bytes(value: number): string {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function Stat({ label: text, value, hint }: { label: string; value: string; hint?: string }): React.ReactElement {
  return (
    <div className="rounded border border-line bg-well px-3 py-2">
      <p className="font-display text-[10px] font-bold uppercase tracking-wider text-slate-400">{text}</p>
      <p className="font-mono text-lg text-white">{value}</p>
      {hint && <p className="text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

function Stages({ status }: { status: PipelineStatus }): React.ReactElement {
  const { spool, forwarder, consumer, archive } = status;
  const fwd = FORWARDER[forwarder.state];
  const con = CONSUMER[consumer.state];
  const used = spool.limit > 0 ? (spool.by_kind.telemetry ?? 0) / spool.limit : 0;
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel title="1. Machine spool" icon="pending_actions" aside={<Badge tone={spool.total > 0 ? "info" : "ok"}>{spool.total > 0 ? "Holding" : "Empty"}</Badge>}>
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Waiting" value={spool.total.toLocaleString()} hint={`${(used * 100).toFixed(1)}% of ${spool.limit.toLocaleString()} tick limit`} />
          <Stat label="Oldest waiting" value={seconds(spool.oldest_age_seconds)} />
          <Stat label="Disk" value={bytes(spool.bytes)} />
          <Stat label="Dropped (reported)" value={forwarder.dropped_total.toLocaleString()} hint={`${forwarder.gaps_total} gap records`} />
        </div>
        {spool.write_failures > 0 && (
          <Notice tone="warning" role="status">
            <p>{spool.write_failures} ticks could not be spooled. Safety was not affected; see the server log.</p>
          </Notice>
        )}
      </Panel>
      <Panel title="2. Kafka forwarder" icon="speed" aside={<Badge tone={fwd.tone}>{fwd.text}</Badge>}>
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Send rate" value={`${number(forwarder.send_rate)}/s`} hint={`cap ${number(forwarder.max_rate)}/s`} />
          <Stat label="New ticks" value={`${number(forwarder.arrival_rate)}/s`} />
          <Stat
            label="Backlog clear in"
            value={spool.drain_eta_seconds === null ? "Not yet" : seconds(spool.drain_eta_seconds)}
            hint={spool.drain_eta_seconds === null && spool.total > 0 ? "not shrinking yet" : undefined}
          />
          <Stat label="Sent" value={forwarder.sent_total.toLocaleString()} hint={forwarder.last_sent_at ? `last ${clockTime(forwarder.last_sent_at)}` : undefined} />
        </div>
        {forwarder.last_error && forwarder.state !== "idle" && forwarder.state !== "draining" && (
          <Notice tone="critical" role="alert">
            <p>{forwarder.last_error}</p>
            {forwarder.retry_in_seconds !== null && <p className="text-xs">Retrying in {seconds(forwarder.retry_in_seconds)}.</p>}
          </Notice>
        )}
      </Panel>
      <Panel title="3. Fleet analytics" icon="insights" aside={<Badge tone={con.tone}>{con.text}</Badge>}>
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Archived ticks" value={archive.ticks.toLocaleString()} hint={`${archive.machines} machines`} />
          <Stat label="Behind Kafka" value={consumer.lag === null ? "Unknown" : consumer.lag.toLocaleString()} hint="records not yet stored" />
          <Stat label="Duplicates ignored" value={consumer.duplicates_total.toLocaleString()} />
          <Stat label="Unreadable skipped" value={consumer.malformed_total.toLocaleString()} />
        </div>
        {consumer.last_error && consumer.state !== "running" && (
          <Notice tone="critical" role="alert">
            <p>{consumer.last_error}</p>
          </Notice>
        )}
      </Panel>
    </div>
  );
}

function Backlog({ status, reference }: { status: PipelineStatus; reference: Reference }): React.ReactElement | null {
  const { spool, archive } = status;
  if (spool.by_machine.length === 0 && archive.recent_gaps.length === 0) return null;
  return (
    <Panel title="Waiting on the machines" icon="history">
      {spool.by_machine.length > 0 && (
        <table className="w-full text-sm">
          <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
            <tr>
              <th className={CELL}>Machine</th>
              <th className={CELL}>Records waiting</th>
              <th className={CELL}>Oldest tick</th>
            </tr>
          </thead>
          <tbody>
            {spool.by_machine.map((m) => (
              <tr key={m.machine_id}>
                <td className={CELL}>{reference.machineLabel(m.machine_id)}</td>
                <td className={`${CELL} font-mono`}>{m.records.toLocaleString()}</td>
                <td className={`${CELL} font-mono`}>{clockTime(m.oldest_event_time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {archive.recent_gaps.length > 0 && (
        <Notice tone="warning" role="status">
          <p>The spool filled up during a long outage, so the oldest raw ticks were dropped. Incidents and shift summaries are never dropped.</p>
          <ul className="list-disc pl-5 text-xs">
            {archive.recent_gaps.map((g) => (
              <li key={`${g.machine_id}-${g.from_ts}`}>
                {reference.machineLabel(g.machine_id)}: {g.dropped_count.toLocaleString()} ticks from {clockTime(g.from_ts)} to{" "}
                {clockTime(g.to_ts)}
              </li>
            ))}
          </ul>
        </Notice>
      )}
    </Panel>
  );
}

function Rollups({ rollups, reference }: { rollups: MachineRollup[]; reference: Reference }): React.ReactElement {
  const totals = rollups.map((m) => {
    const sum = (key: "ticks" | "working_ticks" | "idle_ticks" | "load_cycles" | "fuel_used") =>
      m.points.reduce((acc, p) => acc + p[key], 0);
    const ticks = sum("ticks");
    const rpm = ticks > 0 ? m.points.reduce((acc, p) => acc + p.avg_rpm * p.ticks, 0) / ticks : 0;
    return {
      machine_id: m.machine_id,
      minutes: m.points.length,
      working: ticks > 0 ? sum("working_ticks") / ticks : 0,
      idle: ticks > 0 ? sum("idle_ticks") / ticks : 0,
      rpm,
      fuel: sum("fuel_used"),
      cycles: sum("load_cycles"),
    };
  });
  return (
    <Panel title={`Fleet activity, last ${ROLLUP_MINUTES} minutes`} icon="query_stats" aside={<Legend />}>
      {rollups.length === 0 ? (
        <p className="text-sm text-slate-400">No telemetry has reached the archive yet. Start the simulator to stream live ticks.</p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            {rollups.map((m) => (
              <div key={m.machine_id} className="space-y-1">
                <p className="font-display text-sm font-bold uppercase tracking-wide text-white">{reference.machineLabel(m.machine_id)}</p>
                <MinuteBars points={m.points} title={reference.machineLabel(m.machine_id)} />
              </div>
            ))}
          </div>
          <table className="w-full text-sm" aria-label="Fleet activity totals">
            <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className={CELL}>Machine</th>
                <th className={CELL}>Minutes</th>
                <th className={CELL}>Working</th>
                <th className={CELL}>Idle</th>
                <th className={CELL}>Avg RPM</th>
                <th className={CELL}>Fuel</th>
                <th className={CELL}>Load cycles</th>
              </tr>
            </thead>
            <tbody>
              {totals.map((t) => (
                <tr key={t.machine_id}>
                  <td className={CELL}>{reference.machineLabel(t.machine_id)}</td>
                  <td className={`${CELL} font-mono`}>{t.minutes}</td>
                  <td className={`${CELL} font-mono`}>{(t.working * 100).toFixed(0)}%</td>
                  <td className={`${CELL} font-mono`}>{(t.idle * 100).toFixed(0)}%</td>
                  <td className={`${CELL} font-mono`}>{number(t.rpm)}</td>
                  <td className={`${CELL} font-mono`}>{number(t.fuel, 1, "L")}</td>
                  <td className={`${CELL} font-mono`}>{t.cycles}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SafetyEvents({ status }: { status: PipelineStatus }): React.ReactElement | null {
  const events = status.archive.events_by_type;
  if (events.length === 0) return null;
  return (
    <Panel title="Safety events streamed" icon="warning">
      <table className="w-full text-sm">
        <thead className="font-display text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th className={CELL}>Source</th>
            <th className={CELL}>Type</th>
            <th className={CELL}>Updates</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={`${e.kind}-${e.event_type}`}>
              <td className={CELL}>{e.kind === "incident" ? "Incident" : "Usage pattern"}</td>
              <td className={CELL}>{label(e.event_type)}</td>
              <td className={`${CELL} font-mono`}>{e.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

export default function PipelinePage(): React.ReactElement {
  const token = useToken();
  const reference = useReference();
  const status = usePolledResource(() => adminApi.pipeline(token), [token]);
  const rollups = usePolledResource(() => adminApi.pipelineRollups(token, ROLLUP_MINUTES), [token]);

  const header = (
    <PageHeader
      icon="insights"
      title="Data pipeline"
      subtitle="Live telemetry from each machine, through Kafka, into fleet analytics. Safety runs on the machine and never waits for this."
    />
  );
  if (status.error) return <ErrorNotice error={status.error} onRetry={status.reload} />;
  if (reference.error) return <ErrorNotice error={reference.error} onRetry={reference.reload} />;
  if (!status.data || !reference.data) return <p className="text-sm text-slate-400">Loading</p>;

  if (!status.data.enabled) {
    return (
      <div className="space-y-4">
        {header}
        <Notice tone="info" role="status">
          <p>Streaming is off. Set KAFKA_ENABLED=true in .env, start Kafka with docker compose up -d kafka, and restart the backend.</p>
        </Notice>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {header}
      <p className="font-mono text-xs text-slate-400">
        {status.data.bootstrap_servers} · {status.data.topics.join(", ")}
      </p>
      <Stages status={status.data} />
      <Backlog status={status.data} reference={reference.data} />
      {rollups.error ? (
        <ErrorNotice error={rollups.error} onRetry={rollups.reload} />
      ) : (
        <Rollups rollups={rollups.data ?? []} reference={reference.data} />
      )}
      <SafetyEvents status={status.data} />
    </div>
  );
}
