import { useState } from "react";
import { Link } from "react-router-dom";
import { Pause, Play, Power, Square } from "lucide-react";
import { api, type Dashboard as D } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, RunStateBadge, Spinner, Stat, StatusBadge } from "../components/ui";
import { timeAgo } from "../lib/format";

const LEVEL = { info: "muted", warning: "warn", error: "bad" } as const;

export function Dashboard() {
  const { data: d, error, loading, reload } = useApi(() => api.get<D>("/dashboard"), [], ["task", "activity", "engine", "project", "notification"]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ctl = async (a: "start" | "pause" | "resume" | "stop") => {
    setBusy(true); setErr(null);
    try { await api.post(`/engine/${a}`); await reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  if (loading && !d) return <Spinner />;
  if (!d) return <ErrorNote error={error} />;
  const st = d.engine.state;

  return (
    <>
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <ErrorNote error={err ?? error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Projects" value={d.projects} sub={`${d.executing_projects} executing`} />
        <Stat label="Active tasks" value={d.active_tasks} tone="info" sub="ready / assigned / running" />
        <Stat label="Completed" value={d.completed_tasks} tone="ok" />
        <Stat label="Awaiting input" value={d.awaiting_input} tone={d.awaiting_input ? "warn" : undefined} sub="you or a review" />
        <Stat label="Failed" value={d.failed_tasks} tone={d.failed_tasks ? "bad" : undefined} />
        <Stat label="Accounts" value={`${d.accounts_enabled}/${d.accounts_total}`} sub={`${d.accounts_automated} automated`} />
      </div>

      <Card title="Background service" actions={
        <>
          {st === "stopped" && <Button small variant="primary" busy={busy} onClick={() => ctl("start")}><Power className="h-3.5 w-3.5" />Start</Button>}
          {st === "running" && <Button small busy={busy} onClick={() => ctl("pause")}><Pause className="h-3.5 w-3.5" />Pause</Button>}
          {st === "paused" && <Button small variant="primary" busy={busy} onClick={() => ctl("resume")}><Play className="h-3.5 w-3.5" />Resume</Button>}
          {st !== "stopped" && <Button small variant="danger" busy={busy} onClick={() => ctl("stop")}><Square className="h-3.5 w-3.5" />Stop</Button>}
        </>
      }>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Badge tone={st === "running" ? "ok" : st === "paused" ? "warn" : "bad"}>{st}</Badge>
          <span className="text-muted">Heartbeat {timeAgo(d.engine.heartbeat)} · {d.engine.ws_clients} dashboard{d.engine.ws_clients === 1 ? "" : "s"} connected</span>
        </div>
        <p className="mt-2 text-xs text-muted">This service runs on your computer. Closing this page does not stop it, and it does not depend on GitHub or any online service.</p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Right now">
          {d.current_tasks.length === 0 ? <p className="text-sm text-muted">Nothing is being processed at the moment.</p> : (
            <ul className="space-y-2">{d.current_tasks.map((t) => (
              <li key={t.id} className="flex items-center gap-2 text-sm"><StatusBadge status={t.status} /><RunStateBadge state={t.run_state} /><span className="truncate">{t.title}</span></li>
            ))}</ul>
          )}
        </Card>
        <Card title="Waiting for you">
          {d.awaiting_tasks.length === 0 ? <p className="text-sm text-muted">No tasks need your attention.</p> : (
            <ul className="space-y-2">{d.awaiting_tasks.map((t) => (
              <li key={t.id} className="flex items-center gap-2 text-sm"><StatusBadge status={t.status} /><RunStateBadge state={t.run_state} />
                <Link className="truncate text-accent hover:underline" to={`/projects/${t.project_id}?tab=tasks&task=${t.id}`}>{t.title}</Link></li>
            ))}</ul>
          )}
        </Card>
      </div>

      <Card title="Recent activity" actions={<Link to="/activity" className="text-xs text-accent hover:underline">View all</Link>}>
        {d.recent_activity.length === 0 ? <Empty title="No activity yet" hint="Import a project to get started." /> : (
          <ul className="divide-y divide-line">{d.recent_activity.map((a) => (
            <li key={a.id} className="flex items-center gap-3 py-1.5 text-sm"><Badge tone={LEVEL[a.level as keyof typeof LEVEL] ?? "muted"}>{a.level}</Badge><span className="flex-1 truncate">{a.message}</span><span className="text-xs text-muted">{timeAgo(a.created_at)}</span></li>
          ))}</ul>
        )}
      </Card>
    </>
  );
}
