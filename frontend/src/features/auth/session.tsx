/**
 * Login session: token and identity, kept in sessionStorage so a page
 * reload keeps the operator signed in for this tab only.
 */
import React, { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { LoginResponse } from "@/shared/types/api";

const STORAGE_KEY = "safe2go.session";

export interface Session {
  token: string;
  role: LoginResponse["role"];
  operatorId: string | null;
  /** Signed in at the machine with a shift PIN while the cloud link was down. */
  offline: boolean;
}

interface SessionContextValue {
  session: Session | null;
  signIn: (login: LoginResponse) => void;
  signOut: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

function readStored(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function writeStored(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable (private mode). The session still works in memory.
  }
}

export function SessionProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [session, setSession] = useState<Session | null>(readStored);

  const signIn = useCallback((login: LoginResponse) => {
    const next: Session = {
      token: login.access_token,
      role: login.role,
      operatorId: login.operator_id,
      offline: login.offline,
    };
    writeStored(next);
    setSession(next);
  }, []);

  const signOut = useCallback(() => {
    writeStored(null);
    setSession(null);
  }, []);

  const value = useMemo(() => ({ session, signIn, signOut }), [session, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
