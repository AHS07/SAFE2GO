import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/app/App";
import { SessionProvider } from "@/features/auth/session";
import QuizForm from "@/features/training/QuizForm";
import { parseMarkdown } from "@/shared/components/Markdown";
import type { ModuleListResponse, QuizResultResponse } from "@/shared/types/api";
import { SHIFT, installFakeSocket, mockFetch, signIn } from "./harness";

describe("parseMarkdown", () => {
  it("reads headings, paragraphs, and both list types", () => {
    const blocks = parseMarkdown("# Title\n\nOne\ntwo\n\n## Part\n- a\n- b\n\n1. x\n2. y\n");
    expect(blocks).toEqual([
      { kind: "h1", text: "Title" },
      { kind: "p", text: "One two" },
      { kind: "h2", text: "Part" },
      { kind: "ul", items: ["a", "b"] },
      { kind: "ol", items: ["x", "y"] },
    ]);
  });
});

describe("QuizForm", () => {
  const questions = [
    { question: "First?", options: ["A", "B"] },
    { question: "Second?", options: ["C", "D"] },
  ];

  it("submits only when every question is answered and shows the result", async () => {
    const result: QuizResultResponse = {
      module_id: "m",
      score: 50,
      passed: false,
      pass_pct: 80,
      status: "needs_retry",
      questions: [
        { correct: true, correct_option: 1, explanation: "B is right." },
        { correct: false, correct_option: 0, explanation: "C is right." },
      ],
    };
    const onSubmit = vi.fn(async () => result);
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <QuizForm questions={questions} onSubmit={onSubmit} />
      </SessionProvider>
    );

    const submit = screen.getByRole("button", { name: "Submit answers" });
    await user.click(screen.getByLabelText("B"));
    expect(submit).toBeDisabled();

    await user.click(screen.getByLabelText("D"));
    await user.click(submit);

    expect(onSubmit).toHaveBeenCalledWith([1, 1]);
    expect(await screen.findByText(/Score 50%/)).toBeInTheDocument();
    expect(screen.getByText("C is right.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retake quiz" })).toBeInTheDocument();
  });
});

describe("TrainingListPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    installFakeSocket();
    signIn();
    window.history.pushState({}, "", "/training");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const modules: ModuleListResponse["modules"] = [
    { module_id: "near", title: "Working near people", format: "text", duration_min: 5, status: "not_started", recommended: true },
    { module_id: "fuel", title: "Fuel-efficient operation", format: "text", duration_min: 4, status: "passed", recommended: false },
  ];

  function renderWithModules(list: ModuleListResponse): void {
    mockFetch({
      "GET /api/training/modules": list,
      "GET /api/operator/shift/current": SHIFT,
      "GET /api/operator/tasks": [],
      "GET /api/operator/incidents": [],
      "GET /api/operator/coaching": [],
      "GET /api/emergency": { notice: "n", items: [] },
    });
    render(<App />);
  }

  it("locks modules and explains why while the machine is not parked", async () => {
    renderWithModules({ parked: false, pass_pct: 80, modules });
    expect(await screen.findByText(/Training opens when the machine is parked/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Working near people/ })).not.toBeInTheDocument();
  });

  it("lists recommended modules first and links them while parked", async () => {
    renderWithModules({ parked: true, pass_pct: 80, modules });
    expect(await screen.findByText("Recommended for this shift")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Working near people/ })).toHaveAttribute("href", "/training/near");
    expect(screen.getByText("Passed")).toBeInTheDocument();
  });
});
