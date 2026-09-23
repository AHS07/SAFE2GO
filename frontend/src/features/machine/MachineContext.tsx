/**
 * Live state of the operator's machine and shift.
 *
 * Loads the current shift over REST, then subscribes to the machine's
 * WebSocket. The server sends a full snapshot on every (re)connect and
 * pushes telemetry, incidents, progress, and coaching afterwards. Every
 * alert shown in the UI comes from the backend engines; nothing is
 * derived here except display formatting.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { operatorApi, operatorSocketUrl } from "@/api/operator";
import { WsClient } from "@/api/ws";
import { useSession } from "@/features/auth/session";
import type {
  Coaching,
  Incident,
  MachineStatus,
  RecommendationNotice,
  ServerMessage,
  Shift,
  Task,
  TaskAction,
  TelemetryTick,
} from "@/shared/types/api";

// No tick for this long (real time) means the live feed stopped: sensor state is unknown.
const STALE_AFTER_MS = 5_000;
const HTTP_NOT_FOUND = 404;

export interface MachineContextValue {
  loading: boolean;
  error: unknown;
  shift: Shift | null;
  tasks: Task[];
  incidents: Incident[];
  coaching: Coaching[];
  status: MachineStatus | null;
  connected: boolean;
  degradedRules: string[];
  notice: RecommendationNotice | null;
  completionPromptTaskId: string | null;
  dismissNotice: () => void;
  dismissCompletionPrompt: () => void;
  runTaskAction: (taskId: string, action: TaskAction) => Promise<void>;
  acknowledge: (incidentId: string) => Promise<void>;
  reportIncident: (description: string) => Promise<void>;
  reload: () => void;
}

const MachineContext = createContext<MachineContextValue | null>(null);

function statusFromTick(tick: TelemetryTick): MachineStatus {
  return {
    machine_id: tick.machine_id,
    live: true,
    // Same definition as the backend parked check (park brake on, speed 0).
    parked: tick.park_brake && tick.machine_speed === 0,
    timestamp: tick.timestamp,
    engine_running: tick.engine_running,
    engine_rpm: tick.engine_rpm,
    engine_hours: tick.engine_hours,
    fuel_used: tick.fuel_used,
    hydraulic_active: tick.hydraulic_active,
    machine_speed: tick.machine_speed,
    payload_pct: tick.payload_pct,
    seatbelt_status: tick.seatbelt_status,
    seat_occupied: tick.seat_occupied,
    park_brake: tick.park_brake,
    gear_state: tick.gear_state,
    proximity_distance: tick.proximity_distance,
    ambient_temp: tick.ambient_temp,
    visibility: tick.visibility,
    tilt_angle: tick.tilt_angle,
    task_id: tick.task_id,
  };
}

function unknownStatus(machineId: string): MachineStatus {
  return {
    machine_id: machineId, live: false, parked: false, timestamp: null, engine_running: null,
    engine_rpm: null, engine_hours: null, fuel_used: null, hydraulic_active: null,
    machine_speed: null, payload_pct: null, seatbelt_status: null, seat_occupied: null,
    park_brake: null, gear_state: null, proximity_distance: null, ambient_temp: null,
    visibility: null, tilt_angle: null, task_id: null,
  };
}

export function MachineProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const { session } = useSession();
  const token = session?.token ?? "";

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [shift, setShift] = useState<Shift | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [coaching, setCoaching] = useState<Coaching[]>([]);
  const [status, setStatus] = useState<MachineStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [degradedRules, setDegradedRules] = useState<string[]>([]);
  const [notice, setNotice] = useState<RecommendationNotice | null>(null);
  const [completionPromptTaskId, setCompletionPromptTaskId] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const lastTickAt = useRef(0);

  const refreshTasks = useCallback(() => operatorApi.tasks(token).then(setTasks), [token]);
  const refreshIncidents = useCallback(() => operatorApi.incidents(token).then(setIncidents), [token]);
  const refreshCoaching = useCallback(() => operatorApi.coaching(token).then(setCoaching), [token]);

  // Initial load of the shift and its lists.
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    operatorApi
      .currentShift(token)
      .then(async (current) => {
        const [taskList, incidentList, coachingList] = await Promise.all([
          operatorApi.tasks(token),
          operatorApi.incidents(token),
          operatorApi.coaching(token),
        ]);
        if (!active) return;
        setShift(current);
        setTasks(taskList);
        setIncidents(incidentList);
        setCoaching(coachingList);
        setStatus((prev) => prev ?? unknownStatus(current.machine_id));
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === HTTP_NOT_FOUND) setShift(null);
        else setError(err);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [token, attempt]);

  // Live updates for the shift's machine.
  const machineId = shift?.machine_id;
  useEffect(() => {
    if (!machineId || !token) return;
    const client = new WsClient<ServerMessage>(operatorSocketUrl(machineId, token));
    const offStatus = client.onStatus(setConnected);
    const offMessage = client.onMessage((message) => {
      switch (message.type) {
        case "state":
          if (message.data.shift) setShift(message.data.shift);
          setTasks(message.data.tasks);
          setIncidents(message.data.incidents);
          setCoaching(message.data.coaching);
          setStatus(message.data.status);
          break;
        case "telemetry":
          lastTickAt.current = Date.now();
          setStatus(statusFromTick(message.data));
          break;
        case "incident":
          void refreshIncidents();
          break;
        case "progress": {
          const update = message.data;
          setTasks((prev) =>
            prev.map((t) =>
              t.task_id === update.task_id
                ? { ...t, status: update.status, completed_quantity: update.completed_quantity }
                : t
            )
          );
          if (update.target_reached && update.status === "in_progress") {
            setCompletionPromptTaskId(update.task_id);
          }
          break;
        }
        case "coaching":
          void refreshCoaching();
          if (message.data.recommendation) setNotice(message.data.recommendation);
          break;
        case "recommendation":
          setNotice(message.data);
          break;
        case "safety_degraded":
          setDegradedRules((prev) => (prev.includes(message.data.rule) ? prev : [...prev, message.data.rule]));
          break;
        case "ping":
          break;
      }
    });
    client.connect();
    return () => {
      offStatus();
      offMessage();
      client.disconnect();
    };
  }, [machineId, token, refreshIncidents, refreshCoaching]);

  // Mark sensor state unknown when the live feed goes quiet.
  useEffect(() => {
    if (!machineId) return;
    const timer = setInterval(() => {
      if (lastTickAt.current && Date.now() - lastTickAt.current > STALE_AFTER_MS) {
        lastTickAt.current = 0;
        setStatus(unknownStatus(machineId));
      }
    }, 1_000);
    return () => clearInterval(timer);
  }, [machineId]);

  const runTaskAction = useCallback(
    async (taskId: string, action: TaskAction) => {
      const updated = await operatorApi.taskAction(token, taskId, action);
      setTasks((prev) => prev.map((t) => (t.task_id === updated.task_id ? updated : t)));
      if (action === "complete") setCompletionPromptTaskId(null);
    },
    [token]
  );

  const acknowledge = useCallback(
    async (incidentId: string) => {
      await operatorApi.acknowledge(token, incidentId);
      await refreshIncidents();
    },
    [token, refreshIncidents]
  );

  const reportIncident = useCallback(
    async (description: string) => {
      await operatorApi.reportIncident(token, description);
      await refreshIncidents();
    },
    [token, refreshIncidents]
  );

  const reload = useCallback(() => {
    setAttempt((n) => n + 1);
    void refreshTasks().catch(() => undefined);
  }, [refreshTasks]);

  const value = useMemo<MachineContextValue>(
    () => ({
      loading, error, shift, tasks, incidents, coaching, status, connected, degradedRules, notice,
      completionPromptTaskId,
      dismissNotice: () => setNotice(null),
      dismissCompletionPrompt: () => setCompletionPromptTaskId(null),
      runTaskAction, acknowledge, reportIncident, reload,
    }),
    [loading, error, shift, tasks, incidents, coaching, status, connected, degradedRules, notice,
      completionPromptTaskId, runTaskAction, acknowledge, reportIncident, reload]
  );

  return <MachineContext.Provider value={value}>{children}</MachineContext.Provider>;
}

export function useMachine(): MachineContextValue {
  const value = useContext(MachineContext);
  if (!value) throw new Error("useMachine must be used inside MachineProvider");
  return value;
}
