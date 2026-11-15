import { api, ApiError, qs } from "./api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("api", () => {
  afterEach(() => vi.restoreAllMocks());

  it("builds query strings, skipping empty values", () => {
    expect(qs({ a: 1, b: "", c: null, d: undefined, e: "x y" })).toBe("?a=1&e=x+y");
    expect(qs({})).toBe("");
  });
  it("returns parsed JSON and sends JSON bodies", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: 1 }));
    expect(await api.post("/x", { a: 1 })).toEqual({ ok: 1 });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/x"); expect(init.method).toBe("POST"); expect(init.body).toBe('{"a":1}');
  });
  it("surfaces backend error details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(409, { detail: "the task plan must be approved" }));
    await expect(api.post("/projects/1/start")).rejects.toMatchObject({ status: 409, message: "the task plan must be approved" });
  });
  it("keeps structured error details (restore conflicts)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(409, { detail: { message: "newer changes", changed_files: ["a.py"] } }));
    const e = (await api.post("/checkpoints/1/restore").catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(409); expect(e.message).toBe("newer changes");
    expect((e.detail as { changed_files: string[] }).changed_files).toEqual(["a.py"]);
  });
  it("explains when the local service is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fail"));
    await expect(api.get("/health")).rejects.toThrow(/Cannot reach the Project Pilot service/);
  });
  it("handles 204", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    expect(await api.del("/tasks/1")).toBeUndefined();
  });
});
