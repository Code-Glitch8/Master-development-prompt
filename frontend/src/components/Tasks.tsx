import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Check, Copy, ExternalLink, Pause, Play, Plus, RotateCcw, Trash2, XCircle } from "lucide-react";
import { api, type Account, type Task } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, Modal, Progress, RunStateBadge, Spinner, StatusBadge, ModeBadge } from "./ui";
import { ChangesPanel } from "./Changes";
import { timeAgo } from "../lib/format";

const TERMINAL = ["completed", "cancelled"];

export function TaskDrawer({ taskId, onClose, onChanged }: { taskId: number; onClose: () => void; onChanged: () => void }) {
  const { data: t, error, reload } = useApi(() => api.get<Task>(`/tasks/${taskId}`), [taskId], ["task", "message"]);
  const pid = t?.project_id;
  const { data: prov } = useApi(() => (pid ? api.get<{ accounts: Account[] }>(`/projects/${pid}/providers`) : Promise.resolve(null)), [pid]);
  const { data: allTasks } = useApi(() => (pid ? api.get<Task[]>(`/projects/${pid}/tasks`) : Promise.resolve([] as Task[])), [pid]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [response, setResponse] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [copied, setCopied] = useState(false);
  const [promptText, setPromptText] = useState<{ prompt: string; provider_url: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", description: "", priority: 3, input_prompt: "", depends_on: [] as number[] });

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setErr(null);
    try { await fn(); await reload(true); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  const account = prov?.accounts.find((a) => a.id === t?.assigned_account_id);
  // once a result is recorded (needs_review) the form is hidden; use Retry to supply a different one
  const canSubmit = t && ["awaiting_user", "ready", "assigned", "pending", "failed"].includes(t.status);
  const depKeys = useMemo(() => t?.depends_on.map((d) => allTasks?.find((x) => x.id === d)?.key ?? `#${d}`).join(", "), [t, allTasks]);

  const loadPrompt = async () => {
    const p = await api.get<{ prompt: string; provider_url: string }>(`/tasks/${taskId}/prompt`);
    setPromptText(p);
    return p;
  };
  const copyPrompt = () => act("copy", async () => {
    const p = promptText ?? (await loadPrompt());
    await navigator.clipboard.writeText(p.prompt);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  });
  const submit = () => act("submit", async () => {
    const f = new FormData();
    f.set("response", response);
    files.forEach((x) => f.append("files", x));
    await api.upload(`/tasks/${taskId}/submit`, f);
    setResponse(""); setFiles([]);
  });

  if (!t) return <Modal title="Task" onClose={onClose}>{error ? <ErrorNote error={error} /> : <Spinner />}</Modal>;
  const mode = account?.execution_mode;

  return (
    <Modal title={`${t.key} · ${t.title}`} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={t.status} /><RunStateBadge state={t.run_state} />
          <Badge>Priority {t.priority}</Badge>
          {t.retry_count > 0 && <Badge tone="warn">Retry {t.retry_count}/{t.max_retries}</Badge>}
          {t.next_attempt_at && t.status === "ready" && <Badge tone="info">Next attempt {timeAgo(t.next_attempt_at)}</Badge>}
        </div>
        <ErrorNote error={err ?? error} />
        {t.error && <div className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-sm text-warn whitespace-pre-wrap">{t.error}</div>}

        {!editing ? (
          <div className="space-y-2 text-sm">
            <p className="whitespace-pre-wrap">{t.description || <span className="text-muted">No description.</span>}</p>
            <p className="text-muted">Expected output: {t.expected_output || "–"}</p>
            <p className="text-muted">Depends on: {depKeys || "nothing"} · Capability: {t.required_capability || "any"}</p>
            {t.relevant_files.length > 0 && <p className="break-words font-mono text-xs text-muted">Files: {t.relevant_files.join(", ")}</p>}
          </div>
        ) : (
          <div className="space-y-3">
            <Field label="Title"><input className={inputCls} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
            <Field label="Description"><textarea rows={3} className={inputCls} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
            <Field label="Priority (1 = highest)"><input type="number" min={1} max={5} className={inputCls} value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} /></Field>
            <Field label="Prompt sent to the AI"><textarea rows={8} className={`${inputCls} font-mono text-xs`} value={draft.input_prompt} onChange={(e) => setDraft({ ...draft, input_prompt: e.target.value })} /></Field>
            <Field label="Depends on">
              <div className="flex flex-wrap gap-2">
                {allTasks?.filter((x) => x.id !== t.id).map((x) => (
                  <label key={x.id} className="flex items-center gap-1 text-xs">
                    <input type="checkbox" checked={draft.depends_on.includes(x.id)} onChange={(e) => setDraft({ ...draft, depends_on: e.target.checked ? [...draft.depends_on, x.id] : draft.depends_on.filter((d) => d !== x.id) })} />{x.key}
                  </label>
                ))}
              </div>
            </Field>
            <div className="flex gap-2">
              <Button variant="primary" busy={busy === "save"} onClick={() => act("save", async () => { await api.patch(`/tasks/${t.id}`, draft); setEditing(false); })}>Save</Button>
              <Button onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2 border-t border-line pt-3">
          <Field label="Assigned account">
            <select className={inputCls} value={t.assigned_account_id ?? ""} disabled={t.status === "running"}
              onChange={(e) => act("assign", () => api.post(`/tasks/${t.id}/assign`, { account_id: e.target.value ? Number(e.target.value) : null }))}>
              <option value="">Unassigned</option>
              {prov?.accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.provider_name})</option>)}
            </select>
          </Field>
          {account && <ModeBadge mode={account.execution_mode} />}
          <div className="ml-auto flex flex-wrap gap-1.5">
            {!editing && t.status !== "running" && <Button small onClick={() => { setDraft({ title: t.title, description: t.description, priority: t.priority, input_prompt: t.input_prompt ?? "", depends_on: t.depends_on }); setEditing(true); }}>Edit</Button>}
            {["failed", "awaiting_user", "cancelled", "needs_review", "paused"].includes(t.status) && <Button small busy={busy === "retry"} onClick={() => act("retry", () => api.post(`/tasks/${t.id}/retry`))}><RotateCcw className="h-3.5 w-3.5" />Retry</Button>}
            {["pending", "ready", "assigned"].includes(t.status) && <Button small onClick={() => act("pause", () => api.post(`/tasks/${t.id}/pause`))}><Pause className="h-3.5 w-3.5" />Pause</Button>}
            {t.status === "paused" && <Button small onClick={() => act("resume", () => api.post(`/tasks/${t.id}/resume`))}><Play className="h-3.5 w-3.5" />Resume</Button>}
            {t.status === "needs_review" && <Button small variant="primary" onClick={() => act("approve", () => api.post(`/tasks/${t.id}/approve`))}><Check className="h-3.5 w-3.5" />Approve task</Button>}
            {!TERMINAL.includes(t.status) && <Button small variant="danger" onClick={() => act("cancel", () => api.post(`/tasks/${t.id}/cancel`))}><XCircle className="h-3.5 w-3.5" />Cancel</Button>}
            {t.status !== "running" && <Button small variant="danger" onClick={() => { if (window.confirm("Delete this task?")) void act("delete", async () => { await api.del(`/tasks/${t.id}`); onClose(); }); }}><Trash2 className="h-3.5 w-3.5" />Delete</Button>}
          </div>
        </div>

        {canSubmit && t.status !== "completed" && (
          <Card title={mode === "automated" ? "Provide a result manually (optional)" : "Run this task yourself"}>
            <div className="space-y-3 text-sm">
              {mode === "automated"
                ? <p className="text-muted">This account runs automatically through its official API while the engine is running. You can still paste a result to complete the task by hand.</p>
                : <p className="text-muted">Project Pilot cannot run this account for you. Copy the prompt, run it yourself on the provider's official website, then paste the response (and upload any files it returned) below.</p>}
              <div className="flex flex-wrap gap-2">
                <Button small onClick={copyPrompt}><Copy className="h-3.5 w-3.5" />{copied ? "Copied!" : "Copy prompt"}</Button>
                {(account?.provider_url || promptText?.provider_url) && (
                  <a href={account?.provider_url || promptText?.provider_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel2 px-2 py-1 text-xs font-medium hover:bg-line/60"><ExternalLink className="h-3.5 w-3.5" />Open {account?.provider_name} website</a>
                )}
                <Button small variant="ghost" onClick={() => void loadPrompt()}>Preview prompt</Button>
              </div>
              {promptText && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-2 font-mono text-xs">{promptText.prompt}</pre>}
              <Field label="AI response" hint="Include files as '### FILE: path/to/file' followed by a fenced code block, or upload files / a ZIP below.">
                <textarea rows={6} className={`${inputCls} font-mono text-xs`} value={response} onChange={(e) => setResponse(e.target.value)} placeholder="Paste the AI's response here…" />
              </Field>
              <Field label="Returned files (any type; ZIP keeps its folder structure)">
                <input type="file" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} className="text-xs" />
              </Field>
              <Button variant="primary" busy={busy === "submit"} disabled={!response.trim() && files.length === 0} onClick={submit}>Save result &amp; continue</Button>
            </div>
          </Card>
        )}

        {t.status === "needs_review" && <ChangesPanel projectId={t.project_id} taskId={t.id} />}

        {t.actual_output && (
          <Card title="Recorded output"><pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs">{t.actual_output}</pre></Card>
        )}
        {t.executions && t.executions.length > 0 && (
          <Card title="Execution history">
            <table className="w-full text-xs"><thead className="text-left text-muted"><tr><th>#</th><th>Mode</th><th>Outcome</th><th>Started</th><th>Error</th></tr></thead>
              <tbody>{t.executions.map((e) => <tr key={e.id} className="border-t border-line"><td>{e.attempt}</td><td>{e.mode}</td><td>{e.outcome}</td><td>{timeAgo(e.started_at)}</td><td className="break-words text-bad">{e.error}</td></tr>)}</tbody></table>
          </Card>
        )}
      </div>
    </Modal>
  );
}

function AddTask({ projectId, tasks, onClose, onDone }: { projectId: number; tasks: Task[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ title: "", description: "", priority: 3, depends_on: [] as number[] });
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    try { await api.post(`/projects/${projectId}/tasks`, f); onDone(); onClose(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title="Add task" onClose={onClose}>
      <div className="space-y-3">
        <ErrorNote error={err} />
        <Field label="Title"><input autoFocus className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Description"><textarea rows={3} className={inputCls} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Priority (1 = highest)"><input type="number" min={1} max={5} className={inputCls} value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })} /></Field>
        <Field label="Depends on">
          <div className="flex flex-wrap gap-2">{tasks.map((x) => (
            <label key={x.id} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={f.depends_on.includes(x.id)} onChange={(e) => setF({ ...f, depends_on: e.target.checked ? [...f.depends_on, x.id] : f.depends_on.filter((d) => d !== x.id) })} />{x.key}</label>
          ))}</div>
        </Field>
        <Button variant="primary" disabled={!f.title.trim()} onClick={save}>Add task</Button>
      </div>
    </Modal>
  );
}

export function TaskPanel({ projectId }: { projectId?: number }) {
  const { data: tasks, error, loading, reload } = useApi(() => api.get<Task[]>(projectId ? `/projects/${projectId}/tasks` : "/tasks"), [projectId], ["task", "project"]);
  const { data: accounts } = useApi(() => api.get<Account[]>("/accounts"), []);
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const acct = (id: number | null) => accounts?.find((a) => a.id === id);
  const shown = (tasks ?? []).filter((t) => !statusFilter || t.status === statusFilter);

  const move = async (idx: number, dir: -1 | 1) => {
    if (!projectId || !tasks) return;
    const ids = tasks.map((t) => t.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    try { await api.post(`/projects/${projectId}/tasks/reorder`, { ordered_ids: ids }); void reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <Card
      title={projectId ? "Task plan" : "Task queue"}
      actions={
        <>
          <select aria-label="Filter by status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-line bg-panel2 px-2 py-1 text-xs">
            <option value="">All statuses</option>
            {["pending", "ready", "assigned", "awaiting_user", "running", "paused", "completed", "failed", "needs_review", "cancelled"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
          {projectId && <Button small onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" />Add task</Button>}
        </>
      }
    >
      <ErrorNote error={error ?? err} />
      {loading && !tasks ? <Spinner /> : shown.length === 0 ? (
        <Empty title="No tasks" hint={projectId ? "Write a master prompt and generate a task plan first." : "Tasks appear here once a project has an approved plan."} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm md:min-w-[820px]">
            <thead className="text-left text-xs text-muted">
              <tr><th className="w-14 pb-2"></th><th className="pb-2">Task</th><th>Status</th><th className="hidden md:table-cell">Priority</th><th className="hidden md:table-cell">Depends on</th><th className="hidden md:table-cell">Account</th><th className="hidden w-28 md:table-cell">Progress</th></tr>
            </thead>
            <tbody>
              {shown.map((t, i) => {
                const a = acct(t.assigned_account_id);
                return (
                  <tr key={t.id} className="cursor-pointer border-t border-line hover:bg-panel2/60" onClick={() => setOpen(t.id)}>
                    <td className="py-2" onClick={(e) => e.stopPropagation()}>
                      {projectId && !statusFilter && (
                        <span className="flex"><button aria-label="Move up" className="rounded p-0.5 hover:bg-line/60" onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /></button>
                          <button aria-label="Move down" className="rounded p-0.5 hover:bg-line/60" onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /></button></span>
                      )}
                    </td>
                    <td className="py-2 pr-2"><span className="mr-1.5 font-mono text-xs text-muted">{t.key}</span>{t.title}
                      {t.error && <p className="mt-0.5 max-w-md truncate text-xs text-warn" title={t.error}>{t.error}</p>}</td>
                    <td><div className="flex flex-wrap gap-1"><StatusBadge status={t.status} /><RunStateBadge state={t.run_state} /></div></td>
                    <td className="hidden md:table-cell">{t.priority}</td>
                    <td className="hidden font-mono text-xs text-muted md:table-cell">{t.depends_on.length ? t.depends_on.map((d) => tasks?.find((x) => x.id === d)?.key ?? `#${d}`).join(", ") : "–"}</td>
                    <td className="hidden text-xs md:table-cell">{a ? a.display_name : <span className="text-muted">Unassigned</span>}</td>
                    <td className="hidden md:table-cell"><Progress value={t.status === "completed" ? 1 : t.progress} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {open !== null && <TaskDrawer taskId={open} onClose={() => setOpen(null)} onChanged={() => void reload(true)} />}
      {adding && projectId && <AddTask projectId={projectId} tasks={tasks ?? []} onClose={() => setAdding(false)} onDone={() => void reload(true)} />}
    </Card>
  );
}
