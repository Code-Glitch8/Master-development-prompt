import { formatBytes, MODE_LABEL, pct, RUN_STATE_LABEL, runStateTone, statusTone, timeAgo } from "./format";

describe("format", () => {
  it("formats bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(-1)).toBe("–");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-01-01T00:10:00Z");
    expect(timeAgo("2026-01-01T00:09:58Z", now)).toBe("just now");
    expect(timeAgo("2026-01-01T00:05:00Z", now)).toBe("5m ago");
    expect(timeAgo("2025-12-30T00:10:00Z", now)).toBe("2d ago");
    expect(timeAgo(null)).toBe("never");
    expect(timeAgo("garbage")).toBe("–");
  });
  it("formats percent", () => {
    expect(pct(0.256)).toBe("26%");
    expect(pct(NaN)).toBe("0%");
  });
  it("keeps waiting states distinct (never presented as running)", () => {
    expect(RUN_STATE_LABEL.waiting_user).toBe("Waiting for you");
    expect(RUN_STATE_LABEL.waiting_provider).toBe("Waiting for provider");
    expect(RUN_STATE_LABEL.waiting_ai).toBe("Waiting for AI response");
    expect(new Set(Object.values(RUN_STATE_LABEL)).size).toBe(Object.keys(RUN_STATE_LABEL).length);
    expect(runStateTone("waiting_user")).not.toBe(runStateTone("waiting_ai"));
  });
  it("labels execution modes honestly", () => {
    expect(MODE_LABEL.automated).toMatch(/automated/i);
    expect(MODE_LABEL.user_assisted).toMatch(/user-assisted/i);
    expect(MODE_LABEL.manual_only).toMatch(/manual/i);
  });
  it("maps statuses to tones", () => {
    expect(statusTone("completed")).toBe("ok");
    expect(statusTone("failed")).toBe("bad");
    expect(statusTone("awaiting_user")).toBe("warn");
  });
});
