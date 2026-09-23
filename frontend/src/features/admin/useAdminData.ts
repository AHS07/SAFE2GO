/** Data hooks for the admin screens. */
import { useEffect } from "react";
import { adminApi } from "@/api/admin";
import { useSession } from "@/features/auth/session";
import { type Resource, useResource } from "@/shared/hooks/useResource";
import type { AdminMachine, AdminOperator } from "@/shared/types/api";

// Delivery state changes when the sync worker runs, so lists refresh on a timer.
const REFRESH_MS = 3_000;

export function useToken(): string {
  const { session } = useSession();
  return session?.token ?? "";
}

/** A resource that reloads itself every few seconds. */
export function usePolledResource<T>(load: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const resource = useResource(load, deps);
  const { reload } = resource;
  useEffect(() => {
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload]);
  return resource;
}

export interface Reference {
  operators: AdminOperator[];
  machines: AdminMachine[];
  operatorName: (id: string) => string;
  machineLabel: (id: string) => string;
}

export function useReference(): Resource<Reference> {
  const token = useToken();
  return useResource(async () => {
    const [operators, machines] = await Promise.all([adminApi.operators(token), adminApi.machines(token)]);
    const names = new Map(operators.map((o) => [o.operator_id, o.operator_name]));
    const models = new Map(machines.map((m) => [m.machine_id, m.machine_model]));
    return {
      operators,
      machines,
      operatorName: (id: string) => names.get(id) ?? id.slice(0, 8),
      machineLabel: (id: string) => models.get(id) ?? id.slice(0, 8),
    };
  }, [token]);
}
