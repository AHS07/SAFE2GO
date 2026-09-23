/**
 * Application routes. Operator screens share the live machine and demo-control
 * state; admin screens live under /admin. Each area requires its own role.
 */
import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AdminLayout from "@/features/admin/AdminLayout";
import AssignmentsPage from "@/features/admin/AssignmentsPage";
import ConflictsPage from "@/features/admin/ConflictsPage";
import FleetPage from "@/features/admin/FleetPage";
import ManualSearchPage from "@/features/assistant/ManualSearchPage";
import LoginPage from "@/features/auth/LoginPage";
import RequireSession from "@/features/auth/RequireSession";
import CockpitDashboard from "@/features/cockpit/CockpitDashboard";
import { DemoProvider } from "@/features/demo-controls/DemoContext";
import DispatchPage from "@/features/dispatch/DispatchPage";
import { MachineProvider } from "@/features/machine/MachineContext";
import FleetRadarPage from "@/features/radar/FleetRadarPage";
import SafetyPage from "@/features/safety/SafetyPage";
import ShiftSummaryPage from "@/features/summary/ShiftSummaryPage";
import ModulePage from "@/features/training/ModulePage";
import TrainingListPage from "@/features/training/TrainingListPage";
import ErrorBoundary from "@/shared/components/ErrorBoundary";
import OperatorLayout from "@/shared/components/OperatorLayout";

const PAGES: { path: string; feature: string; element: React.ReactElement }[] = [
  { path: "/", feature: "Cockpit", element: <CockpitDashboard /> },
  { path: "/safety", feature: "Safety", element: <SafetyPage /> },
  { path: "/dispatch", feature: "Tasks", element: <DispatchPage /> },
  { path: "/radar", feature: "Proximity", element: <FleetRadarPage /> },
  { path: "/manuals", feature: "Manual search", element: <ManualSearchPage /> },
  { path: "/training", feature: "Training", element: <TrainingListPage /> },
  { path: "/training/:moduleId", feature: "Training module", element: <ModulePage /> },
  { path: "/summary", feature: "Shift summary", element: <ShiftSummaryPage /> },
];

const ADMIN_PAGES: { path: string; feature: string; element: React.ReactElement }[] = [
  { path: "", feature: "Assignments", element: <AssignmentsPage /> },
  { path: "fleet", feature: "Operators and machines", element: <FleetPage /> },
  { path: "conflicts", feature: "Sync conflicts", element: <ConflictsPage /> },
];

export default function AppRoutes(): React.ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireSession role="operator">
            <DemoProvider>
              <MachineProvider>
                <OperatorLayout />
              </MachineProvider>
            </DemoProvider>
          </RequireSession>
        }
      >
        {PAGES.map((page) => (
          <Route
            key={page.path}
            path={page.path}
            element={<ErrorBoundary feature={page.feature}>{page.element}</ErrorBoundary>}
          />
        ))}
      </Route>
      <Route
        path="/admin"
        element={
          <RequireSession role="admin">
            <DemoProvider>
              <AdminLayout />
            </DemoProvider>
          </RequireSession>
        }
      >
        {ADMIN_PAGES.map((page) => (
          <Route
            key={page.path}
            index={page.path === ""}
            path={page.path || undefined}
            element={<ErrorBoundary feature={page.feature}>{page.element}</ErrorBoundary>}
          />
        ))}
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
