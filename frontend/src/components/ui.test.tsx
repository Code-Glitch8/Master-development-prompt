import { render, screen } from "@testing-library/react";
import { ModeBadge, RunStateBadge, StatusBadge, Progress } from "./ui";
import { buildTree } from "./Files";

describe("badges tell the truth", () => {
  it("shows user-assisted accounts as user-assisted, not automated", () => {
    render(<ModeBadge mode="user_assisted" />);
    expect(screen.getByText("User-assisted execution")).toBeInTheDocument();
    expect(screen.queryByText(/automated/i)).toBeNull();
  });
  it("shows waiting-for-user distinctly from running", () => {
    render(<><StatusBadge status="awaiting_user" /><RunStateBadge state="waiting_user" /></>);
    expect(screen.getByText("Awaiting user")).toBeInTheDocument();
    expect(screen.getByText("Waiting for you")).toBeInTheDocument();
    expect(screen.queryByText("Running")).toBeNull();
  });
  it("renders nothing for idle run state", () => {
    const { container } = render(<RunStateBadge state="idle" />);
    expect(container).toBeEmptyDOMElement();
  });
  it("clamps progress", () => {
    render(<Progress value={7} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("file tree", () => {
  it("nests paths into folders", () => {
    const f = (path: string) => ({ path, size: 1, language: "", is_config: false, is_doc: false, is_protected: false });
    const t = buildTree([f("a/b/c.ts"), f("a/d.ts"), f("e.md")]);
    expect([...t.children.keys()].sort()).toEqual(["a", "e.md"]);
    expect([...t.children.get("a")!.children.keys()].sort()).toEqual(["b", "d.ts"]);
    expect(t.children.get("a")!.children.get("b")!.children.get("c.ts")!.file?.path).toBe("a/b/c.ts");
  });
});
