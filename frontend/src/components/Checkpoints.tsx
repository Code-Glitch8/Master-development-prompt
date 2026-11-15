import { useState } from "react";
import { Download, GitCompare, History, Plus, RotateCcw, Trash2 } from "lucide-react";
import { api, ApiError, type Checkpoint } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, inputCls, Modal, Spinner } from "./ui";
import { timeAgo } from "../lib/format";

interface Compare { files_added: string[]; files_removed: string[]; files_modified: string[]; tasks_status_changed: { id: number; title: string; from: string; to: string }[] }

export function CheckpointsPanel({ projectId }: { projectId: number }) {
  const { data, error, loading, reload } = useApi(() => api.get<Checkpoint[]>(`/projects/${projectId}/checkpoints`), [projectId], ["checkpoint"]);
  const [label, setLabel] = useState("");
  const [sel, setSel] = useState<number[]>([]);
  const [cmp, setCmp] = useState<Compare | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: number; files: string[]; message: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => { setErr(null); setMsg(null); try { await fn(); void reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } };
  const restore = (id: number, force = false) => run(async () => {
    try {
      const r = await api.post<{ pre_restore_checkpoint: number }>(`/checkpoints/${id}/restore`, { force });
      setConflict(null);
      setMsg(`Restored checkpoint #${id}. Your previous state was saved as checkpoint #${r.pre_restore_checkpoint}.`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const d = e.detail as { message: string; changed_files: string[] };
        setConflict({ id, files: d.changed_files, message: d.message });
      } else throw e;
    }
  });
  const toggle = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));

  return (
    <Card title="Checkpoints" actions={
      <>
        <input aria-label="Checkpoint label" className={`${inputCls} w-44`} placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} />
        <Button small variant="primary" onClick={() => run(async () => { await api.post(`/projects/${projectId}/checkpoints`, { label }); setLabel(""); })}><Plus className="h-3.5 w-3.5" />Create</Button>
        <Button small disabled={sel.length !== 2} onClick={() => run(async () => setCmp(await api.get<Compare>(`/checkpoints/compare?a=${Math.min(...sel)}&b=${Math.max(...sel)}`)))}><GitCompare className="h-3.5 w-3.5" />Compare</Button>
      </>
    }>
      <p className="mb-3 text-xs text-muted">Checkpoints are created automatically before and after each task, when work pauses, and when a provider is unavailable. Restoring never silently overwrites newer changes.</p>
      <ErrorNote error={error ?? err} />
      {msg && <div className="mb-3 rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{msg}</div>}
      {loading && !data ? <Spinner /> : data && data.length === 0 ? <Empty icon={<History className="h-6 w-6" />} title="No checkpoints yet" /> : (
        <ul className="divide-y divide-line">{data?.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3 py-2.5">
            <input type="checkbox" aria-label={`Select checkpoint ${c.id}`} checked={sel.includes(c.id)} onChange={() => toggle(c.id)} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">#{c.id} · {c.label}</p>
              <p className="text-xs text-muted">{timeAgo(c.created_at)} · {c.counts.completed}/{c.counts.tasks} tasks done · {c.counts.files} files {c.git_commit && <span className="font-mono">· {c.git_commit.slice(0, 7)}</span>}</p>
            </div>
            <Badge>{c.reason.replace(/_/g, " ")}</Badge>
            <Button small onClick={() => restore(c.id)}><RotateCcw className="h-3.5 w-3.5" />Restore</Button>
            <a href={`/api/checkpoints/${c.id}/export`} className="inline-flex items-center gap-1 rounded-lg border border-line bg-panel2 px-2 py-1 text-xs font-medium hover:bg-line/60"><Download className="h-3.5 w-3.5" />Export</a>
            <Button small variant="ghost" aria-label={`Delete checkpoint ${c.id}`} onClick={() => { if (window.confirm(`Delete checkpoint #${c.id}?`)) void run(() => api.del(`/checkpoints/${c.id}`)); }}><Trash2 className="h-3.5 w-3.5" /></Button>
          </li>
        ))}</ul>
      )}
      {conflict && (
        <Modal title="Newer changes would be lost" onClose={() => setConflict(null)}>
          <div className="space-y-3 text-sm">
            <p>{conflict.message}</p>
            <ul className="max-h-48 overflow-auto rounded-lg border border-line bg-bg p-2 font-mono text-xs">{conflict.files.map((f) => <li key={f}>{f}</li>)}</ul>
            <p className="text-muted">If you continue, an automatic backup checkpoint is created first so you can restore this state again.</p>
            <div className="flex gap-2"><Button variant="danger" onClick={() => restore(conflict.id, true)}>Back up &amp; restore anyway</Button><Button onClick={() => setConflict(null)}>Cancel</Button></div>
          </div>
        </Modal>
      )}
      {cmp && (
        <Modal title="Checkpoint comparison" onClose={() => setCmp(null)}>
          <div className="space-y-3 text-sm">
            {([["Added", cmp.files_added], ["Modified", cmp.files_modified], ["Removed", cmp.files_removed]] as const).map(([t, l]) => (
              <div key={t}><p className="font-medium">{t} files ({l.length})</p>{l.length > 0 && <ul className="font-mono text-xs text-muted">{l.map((f) => <li key={f}>{f}</li>)}</ul>}</div>
            ))}
            <div><p className="font-medium">Task changes ({cmp.tasks_status_changed.length})</p>
              <ul className="text-xs text-muted">{cmp.tasks_status_changed.map((t) => <li key={t.id}>{t.title}: {t.from} → {t.to}</li>)}</ul></div>
          </div>
        </Modal>
      )}
    </Card>
  );
}
