import React from "react";
import { BrowserRouter } from "react-router-dom";
import { SessionProvider } from "@/features/auth/session";
import { EmergencyProvider } from "@/features/emergency/EmergencyContext";
import EmergencyPanel from "@/features/emergency/EmergencyPanel";
import ErrorBoundary from "@/shared/components/ErrorBoundary";
import AppRoutes from "./routes";

export default function App(): React.ReactElement {
  return (
    <BrowserRouter>
      <SessionProvider>
        <EmergencyProvider>
          <AppRoutes />
          <ErrorBoundary feature="Emergency guidance">
            <EmergencyPanel />
          </ErrorBoundary>
        </EmergencyProvider>
      </SessionProvider>
    </BrowserRouter>
  );
}
