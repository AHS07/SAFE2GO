import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../app/App";

const GUIDE = {
  notice: "Follow your site emergency plan.",
  items: [
    { emergency_id: "fire", title: "Machine fire", summary: "Smoke or flames.", steps: ["Stop the machine.", "Leave the cab."] },
    { emergency_id: "medical", title: "Medical emergency", summary: "Someone is unwell.", steps: ["Call for help."] },
  ],
};

function mockFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(GUIDE), { status: 200, headers: { "Content-Type": "application/json" } }))
  );
}

describe("App", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    mockFetch();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends a signed-out user to the login page", () => {
    render(<App />);
    expect(screen.getByText("SAFE2GO")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("opens emergency guidance with one tap, even before login", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Emergency" }));
    const dialog = screen.getByRole("dialog", { name: "Emergency guidance" });

    await user.click(await within(dialog).findByRole("button", { name: /Machine fire/ }));
    expect(within(dialog).getByText("Stop the machine.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uses cached guidance when the machine unit does not answer", async () => {
    localStorage.setItem("safe2go.emergency", JSON.stringify(GUIDE));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Emergency" }));
    expect(await screen.findByRole("button", { name: /Medical emergency/ })).toBeInTheDocument();
  });
});
