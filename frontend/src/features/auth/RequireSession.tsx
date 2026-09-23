import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { Session } from "./session";
import { useSession } from "./session";

export function homeFor(role: Session["role"]): string {
  return role === "admin" ? "/admin" : "/";
}

/** Requires a signed-in user with the given role; anyone else goes to their own home screen. */
export default function RequireSession({
  role,
  children,
}: {
  role: Session["role"];
  children: React.ReactElement;
}): React.ReactElement {
  const { session } = useSession();
  const location = useLocation();
  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (session.role !== role) {
    return <Navigate to={homeFor(session.role)} replace />;
  }
  return children;
}
