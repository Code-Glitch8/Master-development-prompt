import { useEffect, type ReactNode, type ButtonHTMLAttributes } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { MODE_LABEL, RUN_STATE_LABEL, STATUS_LABEL, modeTone, runStateTone, statusTone, type Tone } from "../lib/format";

const TONES: Record<Tone, string> = {
  ok: "bg-ok/15 text-ok border-ok/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  bad: "bg-bad/15 text-bad border-bad/30",
  info: "bg-info/15 text-info border-info/30",
  accent: "bg-accent/15 text-accent border-accent/30",
  muted: "bg-panel2 text-muted border-line",
};

export function Badge({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export const StatusBadge = ({ status }: { status: string }) => <Badge tone={statusTone(status)}>{STATUS_LABEL[status] ?? status}</Badge>;
export const RunStateBadge = ({ state }: { state: string }) =>
  state === "idle" ? null : <Badge tone={runStateTone(state)}>{RUN_STATE_LABEL[state] ?? state}</Badge>;
export const ModeBadge = ({ mode }: { mode: string }) => <Badge tone={modeTone(mode)}>{MODE_LABEL[mode] ?? mode}</Badge>;

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-panel fade-in ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" | "default"; busy?: boolean; small?: boolean };
export function Button({ variant = "default", busy, small, className = "", children, disabled, ...rest }: BtnProps) {
  const v = {
    primary: "bg-accent text-white hover:opacity-90 border-transparent",
    default: "bg-panel2 hover:bg-line/60 border-line",
    ghost: "bg-transparent hover:bg-panel2 border-transparent",
    danger: "bg-bad/15 text-bad hover:bg-bad/25 border-bad/30",
  }[variant];
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition disabled:opacity-50 disabled:cursor-not-allowed ${small ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm"} ${v} ${className}`}
    >
      {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export const inputCls = "w-full rounded-lg border border-line bg-panel2 px-3 py-1.5 text-sm outline-none focus:border-accent placeholder:text-muted";

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:p-8" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={title} className={`w-full ${wide ? "max-w-4xl" : "max-w-xl"} rounded-xl border border-line bg-panel shadow-2xl fade-in`}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <button aria-label="Close" onClick={onClose} className="rounded p-1 hover:bg-panel2"><X className="h-4 w-4" /></button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> <span className="break-words">{error}</span>
    </div>
  );
}

export function Empty({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center text-muted">
      {icon}
      <p className="font-medium text-fg">{title}</p>
      {hint && <p className="max-w-md text-sm">{hint}</p>}
    </div>
  );
}

export const Spinner = () => <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>;

export function Progress({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <div role="progressbar" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100} className="h-1.5 w-full overflow-hidden rounded-full bg-panel2">
      <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${v * 100}%` }} />
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${tone ? TONES[tone].split(" ")[1] : ""}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

export function Diff({ text }: { text: string }) {
  if (!text.trim()) return <p className="text-sm text-muted">No textual differences to show.</p>;
  return (
    <pre className="max-h-96 overflow-auto rounded-lg border border-line bg-bg p-2 font-mono text-xs leading-5">
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "diff-add" : l.startsWith("-") && !l.startsWith("---") ? "diff-del" : l.startsWith("@@") ? "diff-hunk" : ""}>{l || " "}</div>
      ))}
    </pre>
  );
}

export function useConfirm() {
  return (msg: string) => window.confirm(msg);
}
