/**
 * Sign in. Password sign-in goes through the cloud. When the cloud link is
 * down, operators sign in with their PIN against the shift credential
 * cached on the machine.
 */
import React, { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { authApi } from "@/api/auth";
import EmergencyButton from "@/features/emergency/EmergencyButton";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";
import type { LoginResponse } from "@/shared/types/api";
import { homeFor } from "./RequireSession";
import { useSession } from "./session";

const INPUT =
  "min-h-12 w-full rounded border border-line bg-well px-4 text-base text-slate-100 outline-none focus:border-brand focus:ring-1 focus:ring-brand";

type Mode = "password" | "pin";

function loginError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Could not reach the machine unit. Check the connection and try again.";
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <label className="block space-y-1.5">
      <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">{label}</span>
      {children}
    </label>
  );
}

export default function LoginPage(): React.ReactElement {
  const { session, signIn } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<Mode>("password");
  const [cloudDown, setCloudDown] = useState(false);
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    authApi
      .health()
      .then((health) => {
        if (!health.cloud_reachable) {
          setCloudDown(true);
          setMode("pin");
        }
      })
      .catch(() => undefined);
  }, []);

  const from = (location.state as { from?: string } | null)?.from;
  if (session) return <Navigate to={from ?? homeFor(session.role)} replace />;

  const switchMode = (next: Mode) => {
    setMode(next);
    setSecret("");
    setError(null);
  };

  const finish = (login: LoginResponse) => {
    signIn(login);
    const target = from && from.startsWith(homeFor(login.role)) ? from : homeFor(login.role);
    navigate(target, { replace: true });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      finish(mode === "pin" ? await authApi.offlineLogin(username, secret) : await authApi.login(username, secret));
    } catch (err) {
      if (err instanceof ApiError && err.code === "CLOUD_UNAVAILABLE") {
        setCloudDown(true);
        setMode("pin");
        setSecret("");
      }
      setError(loginError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="app-grid-bg flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6 rounded-lg border border-line bg-panel p-6 shadow-hud">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded border border-brand/60 bg-brand/10 font-display text-base font-extrabold text-brand">
              S2G
            </div>
            <div>
              <h1 className="font-display text-2xl font-bold uppercase leading-none tracking-wide text-white">SAFE2GO</h1>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-slate-400">Sign in</p>
            </div>
          </div>
          <EmergencyButton />
        </div>

        {cloudDown && (
          <Notice tone="warning" role="status">
            <p>The cloud connection is down. Operators can sign in with their PIN for the current shift.</p>
          </Notice>
        )}

        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Sign-in method">
          <Button variant={mode === "password" ? "primary" : "secondary"} onClick={() => switchMode("password")} aria-pressed={mode === "password"}>
            Password
          </Button>
          <Button variant={mode === "pin" ? "primary" : "secondary"} onClick={() => switchMode("pin")} aria-pressed={mode === "pin"}>
            Shift PIN
          </Button>
        </div>

        <form className="space-y-4" onSubmit={submit}>
          <Field label="Username">
            <input className={INPUT} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </Field>
          {mode === "password" ? (
            <Field label="Password">
              <input
                className={INPUT}
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>
          ) : (
            <Field label="PIN">
              <input
                className={INPUT}
                type="password"
                inputMode="numeric"
                pattern="[0-9]{4,12}"
                title="4 to 12 digits"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                autoComplete="off"
                required
              />
            </Field>
          )}
          {error && (
            <Notice tone="critical" role="alert">
              <p>{error}</p>
            </Notice>
          )}
          <Button type="submit" variant="primary" className="w-full" disabled={submitting}>
            {submitting ? "Signing in" : "Sign in"}
          </Button>
        </form>
      </div>
    </main>
  );
}
