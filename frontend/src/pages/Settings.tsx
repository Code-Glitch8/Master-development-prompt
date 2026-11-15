import { useState } from "react";
import { api } from "../services/api";
import { useApi } from "../hooks/useApi";
import { useTheme } from "../hooks/useTheme";
import { Button, Card, ErrorNote, Field, inputCls, Spinner } from "../components/ui";

const NOTIFS: [string, string][] = [
  ["notifications.task_completed", "Task completed"], ["notifications.project_completed", "Project completed"],
  ["notifications.provider_attention", "Provider requires attention"], ["notifications.user_action_required", "User action required"],
  ["notifications.recoverable_error", "Recoverable error"], ["notifications.service_stopped", "Background service stopped"],
  ["notifications.export_completed", "Project export completed"],
];

export function Settings() {
  const { data, error, reload, setData } = useApi(() => api.get<Record<string, unknown>>("/settings"), []);
  const { theme, setTheme } = useTheme();
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return error ? <ErrorNote error={error} /> : <Spinner />;

  const save = async (values: Record<string, unknown>) => {
    setErr(null); setMsg(null);
    try { setData(await api.put<Record<string, unknown>>("/settings", { values })); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const toggle = (k: string, label: string) => (
    <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(data[k])} onChange={(e) => save({ [k]: e.target.checked })} />{label}</label>
  );

  return (
    <>
      <h1 className="text-xl font-semibold">Settings</h1>
      <ErrorNote error={err} />
      {msg && <div className="rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{msg}</div>}
      <Card title="Appearance">
        <div className="flex gap-2">{(["dark", "light"] as const).map((t) => (
          <Button key={t} variant={theme === t ? "primary" : "default"} onClick={() => { setTheme(t); void save({ theme: t }); }}>{t === "dark" ? "Dark" : "Light"}</Button>
        ))}</div>
      </Card>
      <Card title="Notifications">
        <div className="space-y-2">{toggle("notifications.enabled", "Enable notifications")}
          {toggle("notifications.windows_toast", "Show Windows desktop notifications")}
          <div className="ml-5 space-y-2 border-l border-line pl-4">{NOTIFS.map(([k, l]) => toggle(k, l))}</div></div>
      </Card>
      <Card title="Execution">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Default max retries for recoverable errors" hint="Retries use exponential backoff and always stop at this limit.">
            <input type="number" min={0} max={10} className={inputCls} value={Number(data.max_retries)} onChange={(e) => save({ max_retries: Number(e.target.value) })} /></Field>
          <Field label="Backoff base (seconds)" hint="Delay doubles after each failed attempt."><input type="number" min={1} className={inputCls} value={Number(data.backoff_base_seconds)} onChange={(e) => save({ backoff_base_seconds: Number(e.target.value) })} /></Field>
        </div>
        <div className="mt-3 space-y-2">{toggle("worker.auto_start", "Start the background engine automatically when the service launches")}
          {toggle("review.require_task_approval", "Require my approval before every task is marked completed")}</div>
      </Card>
      <Card title="Data & backups">
        <p className="mb-3 text-sm text-muted">All projects, conversations, checkpoints and the database live in the <code>data</code> folder next to the application on this computer.</p>
        <Button onClick={async () => { setErr(null); try { const r = await api.post<{ file: string }>("/backup"); setMsg(`Database backed up as ${r.file}`); void reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } }}>Back up database now</Button>
      </Card>
    </>
  );
}
