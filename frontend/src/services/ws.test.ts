import { LiveSocket, type LiveEvent, type LiveStatus } from "./ws";

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  closed = false;
  constructor(public url: string) { FakeWS.instances.push(this); }
  send(s: string) { this.sent.push(s); }
  close() { this.closed = true; this.onclose?.(); }
  open() { this.onopen?.(); }
  msg(o: unknown) { this.onmessage?.({ data: JSON.stringify(o) }); }
}

const mk = () => new LiveSocket({ url: "ws://x/ws", WebSocketImpl: FakeWS as unknown as new (u: string) => WebSocket, baseDelayMs: 100, maxDelayMs: 800, pingMs: 1000 });

describe("LiveSocket", () => {
  beforeEach(() => { FakeWS.instances = []; vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  it("connects and delivers events", () => {
    const s = mk(); const got: LiveEvent[] = [];
    s.on((e) => got.push(e)); s.start();
    FakeWS.instances[0].open();
    FakeWS.instances[0].msg({ type: "task", task_id: 1 });
    expect(s.status).toBe("open");
    expect(got).toEqual([{ type: "task", task_id: 1 }]);
  });

  it("ignores malformed messages", () => {
    const s = mk(); const got: LiveEvent[] = []; s.on((e) => got.push(e)); s.start();
    FakeWS.instances[0].open();
    FakeWS.instances[0].onmessage?.({ data: "not json" });
    expect(got).toEqual([]);
  });

  it("reconnects automatically with exponential backoff capped at the max", () => {
    const s = mk(); s.start();
    FakeWS.instances[0].open();
    FakeWS.instances[0].onclose?.(); // server went away
    expect(s.status).toBe("reconnecting");
    vi.advanceTimersByTime(99); expect(FakeWS.instances.length).toBe(1);
    vi.advanceTimersByTime(1); expect(FakeWS.instances.length).toBe(2); // 100ms
    FakeWS.instances[1].onclose?.(); // still down
    vi.advanceTimersByTime(199); expect(FakeWS.instances.length).toBe(2);
    vi.advanceTimersByTime(1); expect(FakeWS.instances.length).toBe(3); // 200ms
    for (let i = 0; i < 6; i++) { FakeWS.instances.at(-1)!.onclose?.(); }
    expect(s.nextDelay()).toBe(800); // capped
  });

  it("emits 'reconnected' (so views re-fetch real state) and resets backoff", () => {
    const s = mk(); const got: string[] = []; const statuses: LiveStatus[] = [];
    s.on((e) => got.push(e.type)); s.onStatus((x) => statuses.push(x)); s.start();
    FakeWS.instances[0].open();
    expect(got).not.toContain("reconnected"); // first connect is not a reconnect
    FakeWS.instances[0].onclose?.();
    vi.advanceTimersByTime(100);
    FakeWS.instances[1].open();
    expect(got).toContain("reconnected");
    expect(s.status).toBe("open");
    expect(s.nextDelay()).toBe(100); // backoff reset to the base delay
    expect(statuses).toContain("reconnecting");
  });

  it("sends keepalive pings and stops cleanly", () => {
    const s = mk(); s.start(); const ws = FakeWS.instances[0]; ws.open();
    vi.advanceTimersByTime(2100);
    expect(ws.sent.filter((x) => x === "ping").length).toBe(2);
    s.stop();
    expect(s.status).toBe("closed"); expect(ws.closed).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances.length).toBe(1); // no reconnect after stop()
  });
});
