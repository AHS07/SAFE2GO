/** Admin forms: create shift and create task. The backend validates; errors and warnings show inline. */
import React, { useState } from "react";
import { adminApi } from "@/api/admin";
import { ApiError } from "@/api/client";
import { useDemo } from "@/features/demo-controls/DemoContext";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";
import type {
  AdminShift,
  AssignmentWarning,
  MaterialType,
  QuantityUnit,
  TaskType,
  WeatherCategory,
} from "@/shared/types/api";
import { label } from "@/shared/utils/format";
import type { Reference } from "./useAdminData";
import { useToken } from "./useAdminData";

export const INPUT =
  "min-h-12 w-full rounded border border-line bg-well px-3 text-sm text-slate-100 outline-none focus:border-brand focus:ring-1 focus:ring-brand";

const WEATHER: WeatherCategory[] = ["clear", "cloudy", "rain", "fog", "storm"];
const TASK_TYPES: TaskType[] = ["excavation", "material_loading", "trenching"];
const MATERIALS: MaterialType[] = ["clay", "sandy_soil", "gravel", "rock", "mixed_fill", "topsoil"];
// The ETA model is trained on these pairs; the backend rejects any other unit.
export const UNIT_FOR: Record<TaskType, QuantityUnit> = {
  excavation: "m3",
  material_loading: "loads",
  trenching: "m3",
};
const DEFAULT_SHIFT_HOURS = 8;
const HOUR_MS = 3_600_000;

export function Field({ label: text, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <label className="block space-y-1">
      <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">{text}</span>
      {children}
    </label>
  );
}

/** "YYYY-MM-DDTHH:mm" in local time, for datetime-local inputs. */
function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

export function FormError({ error }: { error: unknown }): React.ReactElement | null {
  if (error === null) return null;
  const message = error instanceof ApiError ? error.message : "Could not reach the server. Try again.";
  const fields = error instanceof ApiError ? (error.details.fields as { field: string; problem: string }[] | undefined) : undefined;
  return (
    <Notice tone="critical" role="alert">
      <p>{message}</p>
      {fields && (
        <ul className="list-disc pl-5 text-xs">
          {fields.map((f) => (
            <li key={f.field}>
              {label(f.field)}: {f.problem}
            </li>
          ))}
        </ul>
      )}
    </Notice>
  );
}

export function Warnings({ warnings }: { warnings: AssignmentWarning[] }): React.ReactElement | null {
  if (warnings.length === 0) return null;
  return (
    <Notice tone="warning" role="status">
      {warnings.map((w) => (
        <p key={w.code}>Saved with a warning: {w.message}</p>
      ))}
    </Notice>
  );
}

export function ShiftForm({ reference, onCreated }: { reference: Reference; onCreated: (shift: AdminShift) => void }): React.ReactElement {
  const token = useToken();
  const { sim } = useDemo();
  const base = sim ? new Date(sim.sim_time) : new Date();
  const nextHour = new Date(Math.ceil(base.getTime() / HOUR_MS) * HOUR_MS);
  const [operatorId, setOperatorId] = useState("");
  const [machineId, setMachineId] = useState("");
  const [start, setStart] = useState(toLocalInput(nextHour));
  const [end, setEnd] = useState(toLocalInput(new Date(nextHour.getTime() + DEFAULT_SHIFT_HOURS * HOUR_MS)));
  const [weather, setWeather] = useState<WeatherCategory>("clear");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const shift = await adminApi.createShift(token, {
        operator_id: operatorId,
        machine_id: machineId,
        scheduled_start: fromLocalInput(start),
        scheduled_end: fromLocalInput(end),
        weather_forecast: weather,
      });
      onCreated(shift);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="space-y-3" onSubmit={submit} aria-label="New shift">
      <Field label="Operator">
        <select className={INPUT} value={operatorId} onChange={(e) => setOperatorId(e.target.value)} required>
          <option value="">Choose an operator</option>
          {reference.operators.map((o) => (
            <option key={o.operator_id} value={o.operator_id}>
              {o.operator_name} ({o.qualifications.map((q) => `${label(q.machine_type)} ${q.skill_level}`).join(", ")})
            </option>
          ))}
        </select>
      </Field>
      <Field label="Machine">
        <select className={INPUT} value={machineId} onChange={(e) => setMachineId(e.target.value)} required>
          <option value="">Choose a machine</option>
          {reference.machines.map((m) => (
            <option key={m.machine_id} value={m.machine_id}>
              {m.machine_model} ({label(m.machine_type)}
              {m.machine_status === "maintenance" ? ", in maintenance" : ""})
            </option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Start">
          <input className={INPUT} type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} required />
        </Field>
        <Field label="End">
          <input className={INPUT} type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} required />
        </Field>
      </div>
      <Field label="Weather forecast">
        <select className={INPUT} value={weather} onChange={(e) => setWeather(e.target.value as WeatherCategory)}>
          {WEATHER.map((w) => (
            <option key={w} value={w}>
              {label(w)}
            </option>
          ))}
        </select>
      </Field>
      <FormError error={error} />
      <Button type="submit" variant="primary" className="w-full" disabled={busy}>
        {busy ? "Creating" : "Create shift"}
      </Button>
    </form>
  );
}

export function TaskForm({
  shiftId,
  onCreated,
}: {
  shiftId: string;
  onCreated: (warnings: AssignmentWarning[]) => void;
}): React.ReactElement {
  const token = useToken();
  const [taskType, setTaskType] = useState<TaskType>("excavation");
  const [quantity, setQuantity] = useState("40");
  const [material, setMaterial] = useState<MaterialType>("clay");
  const [start, setStart] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const unit = UNIT_FOR[taskType];

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await adminApi.createTask(token, {
        shift_id: shiftId,
        task_type: taskType,
        target_quantity: Number(quantity),
        quantity_unit: unit,
        material_type: material,
        ...(start ? { scheduled_start: fromLocalInput(start) } : {}),
      });
      onCreated(result.warnings);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="space-y-3" onSubmit={submit} aria-label="New task">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Task type">
          <select className={INPUT} value={taskType} onChange={(e) => setTaskType(e.target.value as TaskType)}>
            {TASK_TYPES.map((t) => (
              <option key={t} value={t}>
                {label(t)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={`Target quantity (${label(unit)})`}>
          <input
            className={INPUT}
            type="number"
            min="1"
            step="any"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label="Material">
          <select className={INPUT} value={material} onChange={(e) => setMaterial(e.target.value as MaterialType)}>
            {MATERIALS.map((m) => (
              <option key={m} value={m}>
                {label(m)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Start (empty: after the last task)">
          <input className={INPUT} type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
      </div>
      <FormError error={error} />
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? "Assigning" : "Assign task"}
      </Button>
    </form>
  );
}
