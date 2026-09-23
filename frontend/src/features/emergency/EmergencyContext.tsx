/**
 * Emergency guidance, fetched once when the app opens and kept in memory
 * and localStorage so it is still there if the machine unit stops answering.
 * Also owns the open/closed state of the emergency panel.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiClient } from "@/api/client";
import type { EmergencyGuide } from "@/shared/types/api";

const CACHE_KEY = "safe2go.emergency";

interface EmergencyContextValue {
  guide: EmergencyGuide | null;
  failed: boolean;
  isOpen: boolean;
  open: () => void;
  close: () => void;
  retry: () => void;
}

const EmergencyContext = createContext<EmergencyContextValue | null>(null);

function readCache(): EmergencyGuide | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as EmergencyGuide) : null;
  } catch {
    return null;
  }
}

function writeCache(guide: EmergencyGuide): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(guide));
  } catch {
    // The in-memory copy is enough if storage is unavailable.
  }
}

export function EmergencyProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [guide, setGuide] = useState<EmergencyGuide | null>(readCache);
  const [failed, setFailed] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    apiClient
      .get<EmergencyGuide>("/api/emergency")
      .then((fresh) => {
        if (!active) return;
        setGuide(fresh);
        setFailed(false);
        writeCache(fresh);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo(
    () => ({ guide, failed, isOpen, open, close, retry }),
    [guide, failed, isOpen, open, close, retry]
  );
  return <EmergencyContext.Provider value={value}>{children}</EmergencyContext.Provider>;
}

export function useEmergency(): EmergencyContextValue {
  const value = useContext(EmergencyContext);
  if (!value) throw new Error("useEmergency must be used inside EmergencyProvider");
  return value;
}
