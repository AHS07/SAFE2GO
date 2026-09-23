import { apiClient } from "@/api/client";
import type {
  ModuleContentResponse,
  ModuleListResponse,
  QuizResultResponse,
} from "@/shared/types/api";

export const trainingApi = {
  listModules: (token: string) =>
    apiClient.get<ModuleListResponse>("/api/training/modules", { token }),

  getModule: (token: string, moduleId: string) =>
    apiClient.get<ModuleContentResponse>(`/api/training/modules/${encodeURIComponent(moduleId)}`, { token }),

  submitQuiz: (token: string, moduleId: string, answers: number[]) =>
    apiClient.post<QuizResultResponse>(
      `/api/training/modules/${encodeURIComponent(moduleId)}/quiz`,
      { answers },
      { token }
    ),
};
