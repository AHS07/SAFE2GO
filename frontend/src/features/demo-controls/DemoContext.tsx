/**
 * Presenter controls: simulator start, stop, speed, scenario injection,
 * and the cloud connection toggle. The backend exposes these only in dev
 * or demo mode; when the endpoints are missing the controls stay hidden.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import { demoApi } from "@/api/operator";
import type { ScenarioName, SimStatus } from "@/shared/types/api";

const POLL_MS = 1_500;
const HTTP_NOT_FOUND = 404;

export interface DemoContextValue {
  available: boolean;
  sim: SimStatus | null;
  cloudReachable: boolean | null;
  /** Sync messages waiting in each outbox; they flush when the cloud link is back.
   *  stream: records waiting in the edge spool for Kafka (null when streaming is off). */
  pending: { cloud: number; edge: number; stream: number | null } | null;
  busy: boolean;
  error: unknown;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  setSpeed: (speed: number) => Promise<void>;
  inject: (scenario: ScenarioName, machineId: string) => Promise<void>;
  setCloudReachable: (reachable: boolean) => Promise<void>;
}

const DemoContext = createContext<DemoContextValue | null>(null);

export function DemoProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [available, setAvailable] = useState(true);
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [cloudReachable, setCloud] = useState<boolean | null>(null);
  const [pending, setPending] = useState<DemoContextValue["pending"]>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    try {
      const [simStatus, system] = await Promise.all([demoApi.simStatus(), demoApi.systemStatus()]);
      setSim(simStatus);
      setCloud(system.cloud_reachable);
      setPending({
        cloud: system.cloud_outbox_pending,
        edge: system.edge_outbox_pending,
        stream: system.stream_spool_pending ?? null,
      });
      setAvailable(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === HTTP_NOT_FOUND) setAvailable(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await action();
        await refresh();
      } catch (err) {
        setError(err);
      } finally {
        setBusy(false);
      }
    },
    [refresh]
  );

  const value = useMemo<DemoContextValue>(
    () => ({
      available,
      sim,
      cloudReachable,
      pending,
      busy,
      error,
      start: () => run(demoApi.start),
      stop: () => run(demoApi.stop),
      setSpeed: (speed) => run(() => demoApi.setSpeed(speed)),
      inject: (scenario, machineId) => run(() => demoApi.inject(scenario, machineId)),
      setCloudReachable: (reachable) => run(() => demoApi.setCloudReachable(reachable)),
    }),
    [available, sim, cloudReachable, pending, busy, error, run]
  );

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export function useDemo(): DemoContextValue {
  const value = useContext(DemoContext);
  if (!value) throw new Error("useDemo must be used inside DemoProvider");
  return value;
}
