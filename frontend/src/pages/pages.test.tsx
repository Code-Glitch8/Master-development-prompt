import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "./Dashboard";
import { Accounts } from "./Accounts";

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

function mockApi(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input).replace(/^\/api/, "").split("?")[0];
    if (url in routes) return json(routes[url]);
    return json({ detail: `unmocked ${url}` }, 404);
  });
}

const engine = { state: "running", loop_alive: true, heartbeat: new Date().toISOString(), current_task_id: null, running_task_ids: [], task_counts: {}, ws_clients: 0 };

describe("pages", () => {
  afterEach(() => vi.restoreAllMocks());

  it("dashboard shows real counts and that closing the page does not stop the service", async () => {
    mockApi({ "/dashboard": { projects: 2, executing_projects: 1, active_tasks: 3, completed_tasks: 7, awaiting_input: 1, failed_tasks: 0, accounts_total: 4, accounts_enabled: 3, accounts_automated: 1, engine, current_tasks: [], awaiting_tasks: [], recent_activity: [] } });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(await screen.findByText("Background service")).toBeInTheDocument();
    expect(screen.getByText("3/4")).toBeInTheDocument();
    expect(screen.getByText(/does not stop it/i)).toBeInTheDocument();
    expect(screen.getByText("Nothing is being processed at the moment.")).toBeInTheDocument();
  });

  it("dashboard explains an unreachable service instead of failing silently", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("down"));
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Cannot reach the Project Pilot service/);
  });

  it("accounts page labels each account's real execution mode and never asks for a password", async () => {
    const acct = (id: number, name: string, mode: string) => ({ id, provider_key: "claude", provider_name: "Claude", provider_url: "https://claude.ai", display_name: name, connection_type: mode === "automated" ? "api_key" : "web_manual", connection_status: "unverified", execution_mode: mode, execution_mode_label: "", permitted_methods: [mode], capabilities: ["code"], enabled: true, notes: "", last_verified_at: null, unavailable_reason: "", assigned_project_ids: [], config: {}, has_credential: mode === "automated", created_at: "" });
    mockApi({
      "/accounts": [acct(1, "Claude Account 1", "user_assisted"), acct(2, "Claude Account 2", "automated")],
      "/providers": [{ id: 1, key: "claude", name: "Claude", website_url: "https://claude.ai", capabilities: ["code"], supported_modes: [], notes: "" }],
      "/connection-types": { web_manual: "x", manual_only: "y", api_key: "z" }, "/projects": [],
    });
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(await screen.findByText("Claude Account 1")).toBeInTheDocument();
    expect(screen.getByText("User-assisted execution")).toBeInTheDocument();
    expect(screen.getByText("Authorized automated execution")).toBeInTheDocument();
    expect(screen.getByText(/never logs in to consumer websites/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /add account/i }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });
});
