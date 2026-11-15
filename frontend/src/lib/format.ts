export const RUN_STATE_LABEL: Record<string, string> = {
  idle: "Idle",
  processing: "Processing locally",
  waiting_ai: "Waiting for AI response",
  waiting_user: "Waiting for you",
  waiting_provider: "Waiting for provider",
  completed: "Execution completed",
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  ready: "Ready",
  assigned: "Assigned",
  awaiting_user: "Awaiting user",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  failed: "Failed",
  needs_review: "Needs review",
  cancelled: "Cancelled",
};

export const MODE_LABEL: Record<string, string> = {
  automated: "Authorized automated execution",
  user_assisted: "User-assisted execution",
  manual_only: "Manual-only interaction",
};

export type Tone = "ok" | "warn" | "bad" | "info" | "muted" | "accent";

export function statusTone(status: string): Tone {
  switch (status) {
    case "completed": return "ok";
    case "failed": return "bad";
    case "running": return "accent";
    case "awaiting_user":
    case "needs_review": return "warn";
    case "ready":
    case "assigned": return "info";
    default: return "muted";
  }
}

export function runStateTone(state: string): Tone {
  switch (state) {
    case "waiting_ai": return "accent";
    case "waiting_user": return "warn";
    case "waiting_provider": return "bad";
    case "processing": return "info";
    case "completed": return "ok";
    default: return "muted";
  }
}

export function modeTone(mode: string): Tone {
  return mode === "automated" ? "ok" : mode === "user_assisted" ? "warn" : "muted";
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "–";
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${u[i]}`;
}

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "–";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function pct(n: number): string {
  return `${Math.round((n || 0) * 100)}%`;
}
