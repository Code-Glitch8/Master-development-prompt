import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, Download, FileArchive, Pause, Play, Rocket, Sparkles, Trash2 } from "lucide-react";
import { api, type Account, type Project } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, ModeBadge, Progress, Spinner, Stat } from "../components/ui";
import { TaskDrawer, TaskPanel } from "../components/Tasks";
import { ConversationsPanel } from "../components/Conversations";
import { CheckpointsPanel } from "../components/Checkpoints";
import { FileExplorerPanel } from "../components/Files";
import { formatBytes, pct } from "../lib/format";

const TABS = [["overview", "Overview"], ["prompt", "Prompt & plan"], ["tasks", "Tasks"], ["providers", "Providers"], ["files", "Files & changes"], ["conversations", "Conversations"], ["checkpoints", "Checkpoints"], ["export", "Export"]] as const;
type Tab = (typeof TABS)[number][0];

interface Prompt { id: number; title: string; body: string; is_draft: boolean; analysis: Analysis; updated_at: string }
interface Analysis { required_changes?: string[]; relevant_files?: string[]; risks?: string[]; required_capabilities?: string[]; expected_deliverables?: string[]; testing_requirements?: string[] }
const CATEGORIES = ["analysis", "planning", "ui", "mobile", "auth", "performance", "bugs", "tests", "docs", "security", "database", "api", "refactor", "general"];

function Overview({ p }: { p: Project }) {
  const inv = p.inventory;
  const langs = Object.entries(inv.languages ?? {});
  const max = Math.max(1, ...langs.map(([, n]) => n));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Files" value={inv.file_count} sub={formatBytes(inv.total_bytes)} />
        <Stat label="Tasks" value={`${p.task_done}/${p.task_total}`} sub={pct(p.progress)} />
        <Stat label="Plan" value={<span className="text-lg">{p.plan_status}</span>} />
        <Stat label="Git" value={<span className="text-lg">{inv.git?.is_repo ? (inv.git.clean ? "clean" : `${inv.git.dirty_files} changed`) : "none"}</span>} sub={inv.git?.branch} />
      </div>
      <Progress value={p.progress} />
      {p.import_warnings.length > 0 && (
        <Card title={<span className="flex items-center gap-2 text-warn"><AlertTriangle className="h-4 w-4" />Import warnings</span>}>
          <ul className="list-disc space-y-1 pl-5 text-sm">{p.import_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Detected">
          <dl className="space-y-3 text-sm">
            <div><dt className="text-xs text-muted">Project directory</dt><dd className="break-all font-mono text-xs">{p.directory}</dd></div>
            <div><dt className="text-xs text-muted">Project types</dt><dd className="mt-1 flex flex-wrap gap-1">{inv.project_types?.length ? inv.project_types.map((t) => <Badge key={t} tone="accent">{t}</Badge>) : "–"}</dd></div>
            <div><dt className="text-xs text-muted">Frameworks</dt><dd className="mt-1 flex flex-wrap gap-1">{inv.frameworks?.length ? inv.frameworks.map((t) => <Badge key={t} tone="info">{t}</Badge>) : "–"}</dd></div>
            <div><dt className="text-xs text-muted">Package managers</dt><dd className="mt-1 flex flex-wrap gap-1">{inv.package_managers?.length ? inv.package_managers.map((t) => <Badge key={t}>{t}</Badge>) : "–"}</dd></div>
            {inv.git?.last_commit && <div><dt className="text-xs text-muted">Last commit</dt><dd className="font-mono text-xs">{inv.git.last_commit}</dd></div>}
          </dl>
        </Card>
        <Card title="Languages">
          {langs.length === 0 ? <p className="text-sm text-muted">No source files detected.</p> : <ul className="space-y-2">{langs.map(([l, n]) => (
            <li key={l} className="text-sm"><div className="flex justify-between"><span>{l}</span><span className="text-muted">{n} files</span></div><Progress value={n / max} /></li>
          ))}</ul>}
        </Card>
        <Card title="Dependencies">
          {Object.keys(inv.dependencies ?? {}).length === 0 ? <p className="text-sm text-muted">None detected.</p> : Object.entries(inv.dependencies).map(([m, d]) => (
            <div key={m} className="mb-2"><p className="text-xs font-medium text-muted">{m} ({d.length})</p><p className="break-words font-mono text-xs">{d.slice(0, 40).join(", ")}{d.length > 40 ? " …" : ""}</p></div>
          ))}
        </Card>
        <Card title="Configuration & documentation">
          <p className="mb-1 text-xs text-muted">Config files</p><p className="break-words font-mono text-xs">{inv.config_files?.slice(0, 20).join(", ") || "–"}</p>
          <p className="mb-1 mt-3 text-xs text-muted">Existing documentation</p><p className="break-words font-mono text-xs">{inv.doc_files?.slice(0, 20).join(", ") || "–"}</p>
        </Card>
      </div>
    </div>
  );
}

function PromptPlan({ p, reloadProject }: { p: Project; reloadProject: () => void }) {
  const prompts = useApi(() => api.get<Prompt[]>(`/projects/${p.id}/prompts`), [p.id]);
  const templates = useApi(() => api.get<{ id: string; title: string; body: string }[]>("/prompt-templates"), []);
  const [body, setBody] = useState("");
  const [title, setTitle] = useState("Master prompt");
  const [editId, setEditId] = useState<number | null>(null);
  const [instr, setInstr] = useState(p.instructions);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const latest = prompts.data?.find((x) => !x.is_draft) ?? prompts.data?.[0];

  useEffect(() => { if (latest && editId === null && body === "") { setBody(latest.body); setTitle(latest.title); setEditId(latest.id); setAnalysis(latest.analysis); } }, [latest]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (name: string, fn: () => Promise<unknown>) => { setBusy(name); setErr(null); setMsg(null); try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); } };
  const savePrompt = async (draft: boolean) => {
    const payload = { title, body, is_draft: draft };
    const r = editId ? await api.put<Prompt>(`/prompts/${editId}`, payload) : await api.post<Prompt>(`/projects/${p.id}/prompts`, payload);
    setEditId(r.id); void prompts.reload(true); return r;
  };
  const generate = () => run("plan", async () => {
    await savePrompt(false);
    await api.patch(`/projects/${p.id}`, { instructions: instr });
    const r = await api.post<{ analysis: Analysis }>(`/projects/${p.id}/plan`, { prompt_id: editId ?? undefined });
    setAnalysis(r.analysis); setMsg("Task plan generated. Review the Tasks tab, then approve the plan."); reloadProject();
  });
  const attach = (files: FileList | null) => run("attach", async () => {
    for (const f of Array.from(files ?? [])) { const fd = new FormData(); fd.set("file", f); await api.upload(`/projects/${p.id}/attachments`, fd); }
    setMsg("Attached. Any resulting file changes are waiting for your review in Files & changes (nothing was applied).");
  });
  const control = (a: "plan/approve" | "start" | "pause" | "resume") => run(a, async () => { await api.post(`/projects/${p.id}/${a}`); reloadProject(); });

  const hasPlan = p.task_total > 0;
  const st = p.plan_status;

  return (
    <div className="space-y-4">
      <ErrorNote error={err} />
      {msg && <div className="rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{msg}</div>}
      <Card title="Master prompt" actions={
        <select aria-label="Prompt template" className="rounded-lg border border-line bg-panel2 px-2 py-1 text-xs" value="" onChange={(e) => { const t = templates.data?.find((x) => x.id === e.target.value); if (t) { setBody(t.body); setTitle(t.title); setEditId(null); } }}>
          <option value="">Insert template…</option>{templates.data?.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
        </select>}>
        <div className="space-y-3">
          <input aria-label="Prompt title" className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} />
          <textarea aria-label="Master prompt" rows={12} className={`${inputCls} font-mono text-sm`} placeholder="Describe the whole job in one instruction, e.g. “Redesign the dashboard, improve mobile responsiveness, fix authentication errors, optimize performance and test the entire application.”" value={body} onChange={(e) => setBody(e.target.value)} />
          <Field label="Project-specific instructions (added to every task)"><textarea rows={3} className={inputCls} value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="Coding conventions, files not to touch, test commands…" /></Field>
          <Field label="Attach files or ZIPs" hint="Saved with the project and staged for your review. Nothing is applied automatically."><input type="file" multiple onChange={(e) => void attach(e.target.files)} className="text-sm" /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={p.auto_approve_plan} onChange={(e) => run("auto", async () => { await api.patch(`/projects/${p.id}`, { auto_approve_plan: e.target.checked }); reloadProject(); })} />Automatically approve the generated plan <span className="text-xs text-muted">(off by default: files are never touched before you approve)</span></label>
          <div className="flex flex-wrap gap-2">
            <Button busy={busy === "save"} disabled={!body.trim()} onClick={() => run("save", async () => { await savePrompt(true); await api.patch(`/projects/${p.id}`, { instructions: instr }); setMsg("Draft saved."); })}>Save draft</Button>
            <Button variant="primary" busy={busy === "plan"} disabled={!body.trim() || ["executing", "completed"].includes(st)} onClick={generate}><Sparkles className="h-4 w-4" />{hasPlan ? "Regenerate plan" : "Analyse & generate plan"}</Button>
          </div>
        </div>
      </Card>

      {analysis && analysis.required_changes && (
        <Card title="Analysis">
          <div className="grid gap-4 text-sm md:grid-cols-2">
            {([["Required changes", analysis.required_changes], ["Risks", analysis.risks], ["Relevant files", analysis.relevant_files], ["Required capabilities", analysis.required_capabilities], ["Expected deliverables", analysis.expected_deliverables], ["Testing requirements", analysis.testing_requirements]] as const).map(([t, l]) => (
              <div key={t}><p className="mb-1 text-xs font-medium uppercase text-muted">{t}</p>{l && l.length ? <ul className="list-disc space-y-0.5 pl-5">{l.map((x, i) => <li key={i} className="break-words">{x}</li>)}</ul> : <p className="text-muted">–</p>}</div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Execution">
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone={st === "executing" ? "accent" : st === "completed" ? "ok" : "muted"}>Plan: {st}</Badge>
          {st === "planned" && <Button variant="primary" busy={busy === "plan/approve"} onClick={() => control("plan/approve")}>Approve plan</Button>}
          {["approved"].includes(st) && <Button variant="primary" busy={busy === "start"} onClick={() => control("start")}><Rocket className="h-4 w-4" />Start execution</Button>}
          {st === "executing" && <Button busy={busy === "pause"} onClick={() => control("pause")}><Pause className="h-4 w-4" />Pause</Button>}
          {st === "paused" && <Button variant="primary" busy={busy === "resume"} onClick={() => control("resume")}><Play className="h-4 w-4" />Resume</Button>}
        </div>
        <p className="mt-2 text-xs text-muted">{st === "draft" ? "Generate a plan first." : st === "planned" ? "No project file is touched until you approve the plan." : st === "approved" ? "Approved. Assign accounts to tasks (Providers tab), then start." : "Tasks run in dependency order. Tasks on user-assisted accounts wait for you to paste the result."}</p>
      </Card>
    </div>
  );
}

function Providers({ p, reloadProject }: { p: Project; reloadProject: () => void }) {
  const all = useApi(() => api.get<Account[]>("/accounts"), []);
  const cur = useApi(() => api.get<{ account_ids: number[]; category_accounts: Record<string, number> }>(`/projects/${p.id}/providers`), [p.id]);
  const [sel, setSel] = useState<number[] | null>(null);
  const [cat, setCat] = useState<Record<string, number> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const ids = sel ?? cur.data?.account_ids ?? [];
  const cats = cat ?? cur.data?.category_accounts ?? {};
  const chosen = (all.data ?? []).filter((a) => ids.includes(a.id));

  const save = async () => {
    setErr(null); setSaved(false);
    try { await api.put(`/projects/${p.id}/providers`, { account_ids: ids, category_accounts: Object.fromEntries(Object.entries(cats).filter(([, v]) => ids.includes(v))) }); setSaved(true); setSel(null); setCat(null); void cur.reload(true); reloadProject(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="space-y-4">
      <ErrorNote error={err ?? cur.error} />
      <Card title="Accounts available to this project" actions={<Link to="/accounts" className="text-xs text-accent hover:underline">Manage accounts</Link>}>
        {!all.data?.length ? <Empty title="No accounts yet" hint={<>Add accounts on the <Link className="text-accent" to="/accounts">AI Accounts</Link> page first.</>} /> : (
          <ul className="divide-y divide-line">{all.data.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 py-2"><label className="flex flex-1 items-center gap-2 text-sm"><input type="checkbox" checked={ids.includes(a.id)} onChange={(e) => setSel(e.target.checked ? [...ids, a.id] : ids.filter((x) => x !== a.id))} />{a.display_name}<span className="text-xs text-muted">({a.provider_name})</span></label><ModeBadge mode={a.execution_mode} />{!a.enabled && <Badge tone="bad">disabled</Badge>}</li>
          ))}</ul>
        )}
      </Card>
      <Card title="Default account by task type">
        <p className="mb-3 text-xs text-muted">Used when the plan is approved to pre-assign tasks (e.g. code analysis → Claude, documentation → Gemini, review → ChatGPT). You can change any individual task. Accounts are never switched silently.</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{CATEGORIES.map((c) => (
          <Field key={c} label={c}><select className={inputCls} value={cats[c] ?? ""} onChange={(e) => { const n = { ...cats }; if (e.target.value) n[c] = Number(e.target.value); else delete n[c]; setCat(n); }}>
            <option value="">Automatic</option>{chosen.map((a) => <option key={a.id} value={a.id}>{a.display_name}</option>)}</select></Field>
        ))}</div>
      </Card>
      <div className="flex items-center gap-3"><Button variant="primary" onClick={save}>Save provider settings</Button>{saved && <span className="text-sm text-ok">Saved</span>}</div>
    </div>
  );
}

function ExportTab({ p }: { p: Project }) {
  const [orig, setOrig] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [made, setMade] = useState<{ file: string; download: string; size: number } | null>(null);
  const exports = useApi(() => api.get<{ file: string; size: number; created: string }[]>("/exports"), []);
  const handoff = useApi(() => api.get<{ markdown: string }>(`/projects/${p.id}/handoff`), [p.id], ["task"]);
  const make = async (kind: string) => { setBusy(kind); setErr(null); try { setMade(await api.post(kind === "project" ? `/projects/${p.id}/export` : `/projects/${p.id}/export/${kind}`, kind === "project" ? { include_original: orig } : {})); void exports.reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); } };
  return (
    <div className="space-y-4">
      <ErrorNote error={err} />
      <Card title="Export">
        <p className="mb-3 text-sm text-muted">Exports include the project, a final report, a task report, handoff files, conversations and history. Credentials, environment files, keys and databases are never included.</p>
        <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={orig} onChange={(e) => setOrig(e.target.checked)} />Also include the original imported files</label>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" busy={busy === "project"} onClick={() => make("project")}><FileArchive className="h-4 w-4" />Export finished project (ZIP)</Button>
          {["conversations", "history", "checkpoints", "handoff"].map((k) => <Button key={k} busy={busy === k} onClick={() => make(k)}>Export {k}</Button>)}
        </div>
        {made && <p className="mt-3 text-sm">Created <a className="text-accent underline" href={made.download}>{made.file}</a> ({formatBytes(made.size)})</p>}
      </Card>
      <Card title="Previous exports">{exports.data?.length ? <ul className="divide-y divide-line text-sm">{exports.data.map((e) => (
        <li key={e.file} className="flex items-center justify-between py-1.5"><span className="truncate">{e.file}</span><a className="inline-flex items-center gap-1 text-xs text-accent" href={`/api/exports/${e.file}`}><Download className="h-3 w-3" />{formatBytes(e.size)}</a></li>
      ))}</ul> : <p className="text-sm text-muted">None yet.</p>}</Card>
      <Card title="Current HANDOFF.md preview"><pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-xs">{handoff.data?.markdown}</pre></Card>
    </div>
  );
}

export function ProjectDetail() {
  const { id } = useParams();
  const pid = Number(id);
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const tab = (TABS.some(([k]) => k === sp.get("tab")) ? sp.get("tab") : "overview") as Tab;
  const { data: p, error, loading, reload } = useApi(() => api.get<Project>(`/projects/${pid}`), [pid], ["project", "task", "file_changes"]);
  const openTask = sp.get("task") ? Number(sp.get("task")) : null;
  if (loading && !p) return <Spinner />;
  if (!p) return <ErrorNote error={error ?? "Project not found"} />;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><Link to="/projects" className="text-xs text-muted hover:text-fg">← Projects</Link><h1 className="text-xl font-semibold">{p.name}</h1></div>
        <div className="flex gap-2"><Button onClick={() => setSp({ tab: "export" })}><Download className="h-4 w-4" />Export</Button>
          <Button variant="danger" aria-label="Delete project" onClick={async () => { if (window.confirm(`Delete "${p.name}" and its project files? Exports are kept.`)) { await api.del(`/projects/${p.id}`); nav("/projects"); } }}><Trash2 className="h-4 w-4" /></Button></div>
      </div>
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line">{TABS.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={tab === k} onClick={() => setSp({ tab: k })} className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === k ? "border-accent font-medium text-accent" : "border-transparent text-muted hover:text-fg"}`}>{l}</button>
      ))}</div>
      {tab === "overview" && <Overview p={p} />}
      {tab === "prompt" && <PromptPlan p={p} reloadProject={() => void reload(true)} />}
      {tab === "tasks" && <TaskPanel projectId={p.id} />}
      {tab === "providers" && <Providers p={p} reloadProject={() => void reload(true)} />}
      {tab === "files" && <FileExplorerPanel projectId={p.id} />}
      {tab === "conversations" && <ConversationsPanel projectId={p.id} />}
      {tab === "checkpoints" && <CheckpointsPanel projectId={p.id} />}
      {tab === "export" && <ExportTab p={p} />}
      {openTask !== null && tab !== "tasks" && <TaskDrawer taskId={openTask} onClose={() => setSp({ tab })} onChanged={() => void reload(true)} />}
    </>
  );
}
