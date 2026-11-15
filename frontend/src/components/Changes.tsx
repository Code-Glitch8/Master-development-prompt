import { useState } from "react";
import { Check, FileDiff, ShieldAlert, X } from "lucide-react";
import { api, qs, type FileChange } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Diff, Empty, ErrorNote, Spinner } from "./ui";

const TONE: Record<string, "ok" | "info" | "bad" | "muted"> = { added: "ok", modified: "info", deleted: "bad", unchanged: "muted" };

function ChangeRow({ c, onDone }: { c: FileChange; onDone: () => void }) {
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = async () => {
    if (diff !== null) return setDiff(null);
    try { setDiff((await api.get<{ diff: string }>(`/changes/${c.id}/diff`)).diff); } catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
  };
  const decide = async (approve: boolean) => {
    setBusy(true); setErr(null);
    try { await api.post(`/changes/${c.id}/decide`, { approve }); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={TONE[c.change_type] ?? "muted"}>{c.change_type}</Badge>
        <code className="break-all text-sm">{c.path}</code>
        {c.protected && <Badge tone="warn" title="Credentials, env files, keys and databases are never overwritten"><ShieldAlert className="h-3 w-3" /> protected</Badge>}
        <span className="text-xs text-muted">{c.summary}</span>
        <span className="ml-auto flex gap-1.5">
          {c.change_type !== "unchanged" && <Button small variant="ghost" onClick={toggle}><FileDiff className="h-3.5 w-3.5" />Diff</Button>}
          {c.decision === "pending" ? (
            <>
              <Button small variant="primary" busy={busy} onClick={() => decide(true)}><Check className="h-3.5 w-3.5" />Approve</Button>
              <Button small busy={busy} onClick={() => decide(false)}><X className="h-3.5 w-3.5" />Reject</Button>
            </>
          ) : <Badge tone={c.decision === "approved" ? "ok" : "muted"}>{c.decision}</Badge>}
        </span>
      </div>
      <ErrorNote error={err} />
      {diff !== null && <Diff text={diff} />}
    </li>
  );
}

export function ChangesPanel({ projectId, taskId }: { projectId: number; taskId?: number }) {
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [bulkErr, setBulkErr] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi(
    () => api.get<{ summary: Record<string, number>; changes: FileChange[] }>(`/projects/${projectId}/changes${qs({ decision: filter === "pending" ? "pending" : undefined, task_id: taskId })}`),
    [projectId, taskId, filter], ["file_changes", "task"],
  );
  const bulk = async (approve: boolean) => {
    setBulkErr(null);
    try {
      const ids = data?.changes.filter((c) => c.decision === "pending").map((c) => c.id);
      const r = await api.post<{ decided: number; errors: { path: string; error: string }[] }>(`/projects/${projectId}/changes/decide-all`, { approve, ids });
      if (r.errors.length) setBulkErr(r.errors.map((e) => `${e.path}: ${e.error}`).join("\n"));
      void reload();
    } catch (e) { setBulkErr(e instanceof Error ? e.message : String(e)); }
  };
  const pending = data?.changes.filter((c) => c.decision === "pending").length ?? 0;

  return (
    <Card
      title="File changes awaiting your review"
      actions={
        <>
          <select aria-label="Filter changes" value={filter} onChange={(e) => setFilter(e.target.value as "pending" | "all")} className="rounded-lg border border-line bg-panel2 px-2 py-1 text-xs">
            <option value="pending">Pending</option><option value="all">All</option>
          </select>
          <Button small variant="primary" disabled={!pending} onClick={() => bulk(true)}>Approve all ({pending})</Button>
          <Button small disabled={!pending} onClick={() => bulk(false)}>Reject all</Button>
        </>
      }
    >
      <p className="mb-3 text-xs text-muted">Nothing returned by an AI session is written to your project until you approve it. Existing files are backed up first.</p>
      {data && (
        <div className="mb-2 flex gap-3 text-xs text-muted">
          {(["added", "modified", "deleted", "unchanged"] as const).map((k) => <span key={k}>{data.summary[k] ?? 0} {k}</span>)}
        </div>
      )}
      <ErrorNote error={error ?? bulkErr} />
      {loading && !data ? <Spinner /> : data && data.changes.length === 0 ? (
        <Empty title={filter === "pending" ? "No pending changes" : "No file changes yet"} hint="When a task returns files, they appear here as added / modified / deleted for you to review." />
      ) : (
        <ul className="divide-y divide-line">{data?.changes.map((c) => <ChangeRow key={c.id} c={c} onDone={() => void reload(true)} />)}</ul>
      )}
    </Card>
  );
}
