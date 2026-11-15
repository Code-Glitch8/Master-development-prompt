/** Auto-reconnecting WebSocket for live dashboard updates.
 *  The backend worker never depends on this connection; on reconnect, subscribers are told so
 *  they can re-fetch the true state over REST. */
export type LiveEvent = { type: string; [k: string]: unknown };
export type LiveStatus = "connecting" | "open" | "reconnecting" | "closed";

type WSCtor = new (url: string) => WebSocket;

export interface LiveOptions {
  url?: string;
  WebSocketImpl?: WSCtor;
  baseDelayMs?: number;
  maxDelayMs?: number;
  pingMs?: number;
}

export class LiveSocket {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pinger: ReturnType<typeof setInterval> | null = null;
  private stopped = false;
  private everOpened = false;
  status: LiveStatus = "connecting";
  private handlers = new Set<(e: LiveEvent) => void>();
  private statusHandlers = new Set<(s: LiveStatus) => void>();
  private opts: Required<Omit<LiveOptions, "url" | "WebSocketImpl">> & { url?: string; WebSocketImpl?: WSCtor };

  constructor(opts: LiveOptions = {}) {
    this.opts = { baseDelayMs: 500, maxDelayMs: 10_000, pingMs: 20_000, ...opts };
  }

  private url(): string {
    if (this.opts.url) return this.opts.url;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws`;
  }

  start(): void {
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.pinger) clearInterval(this.pinger);
    this.ws?.close();
    this.setStatus("closed");
  }

  on(h: (e: LiveEvent) => void): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  onStatus(h: (s: LiveStatus) => void): () => void {
    this.statusHandlers.add(h);
    h(this.status);
    return () => this.statusHandlers.delete(h);
  }

  nextDelay(): number {
    return Math.min(this.opts.maxDelayMs, this.opts.baseDelayMs * 2 ** Math.max(0, this.attempts - 1));
  }

  private setStatus(s: LiveStatus) {
    this.status = s;
    this.statusHandlers.forEach((h) => h(s));
  }

  private emit(e: LiveEvent) {
    this.handlers.forEach((h) => h(e));
  }

  private open() {
    const Impl = this.opts.WebSocketImpl ?? WebSocket;
    this.setStatus(this.everOpened ? "reconnecting" : "connecting");
    let ws: WebSocket;
    try {
      ws = new Impl(this.url());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      const wasReconnect = this.everOpened;
      this.everOpened = true;
      this.attempts = 0;
      this.setStatus("open");
      if (this.pinger) clearInterval(this.pinger);
      this.pinger = setInterval(() => { try { ws.send("ping"); } catch { /* closed */ } }, this.opts.pingMs);
      if (wasReconnect) this.emit({ type: "reconnected" });
    };
    ws.onmessage = (m) => {
      try { this.emit(JSON.parse(String(m.data)) as LiveEvent); } catch { /* ignore malformed */ }
    };
    ws.onclose = () => {
      if (this.pinger) clearInterval(this.pinger);
      if (!this.stopped) this.scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch { /* noop */ } };
  }

  private scheduleReconnect() {
    this.attempts += 1;
    this.setStatus("reconnecting");
    this.timer = setTimeout(() => { if (!this.stopped) this.open(); }, this.nextDelay());
  }
}

export const live = new LiveSocket();
