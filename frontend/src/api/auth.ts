/** Sign-in endpoints. Password login goes through the cloud; PIN login is checked at the machine. */
import { apiClient } from "./client";
import type { Health, LoginResponse } from "@/shared/types/api";

export const authApi = {
  login: (username: string, password: string) =>
    apiClient.post<LoginResponse>("/api/auth/login", { username, password }),
  offlineLogin: (username: string, pin: string) =>
    apiClient.post<LoginResponse>("/api/auth/offline-login", { username, pin }),
  health: () => apiClient.get<Health>("/api/system/health"),
};
