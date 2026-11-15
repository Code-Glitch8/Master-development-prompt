import { useState } from "react";
import { api, qs, type ActivityItem } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Card, Empty, ErrorNote, inputCls, Spinner } from "../components/ui";
import { ProjectPicker } from "../components/ProjectPicker";
import { timeAgo } from "../lib/format";

export function Activity() {
  const [project, setProject] = useState<number | null>(null);
  const [level, setLevel] = useState("");
  const [text, setText] = useState("");
  const { data, error, loading } = useApi(() => api.get<ActivityItem[]>(`/activity${qs({ limit: 500, project_id: project, level })}`), [project, level], ["activity"]);
  const rows = (data ?? []).filter((a) => !text || a.message.toLowerCase().includes(text.toLowerCase()) || a.event.includes(text.toLowerCase()));
  return (
    <>
      <h1 className="text-xl font-semibold">Activity logs</h1>
      <Card actions={<>
        <input aria-label="Filter text" className={`${inputCls} w-44`} placeholder="Filter…" value={text} onChange={(e) => setText(e.target.value)} />
        <select aria-label="Level" className={`${inputCls} w-32`} value={level} onChange={(e) => setLevel(e.target.value)}><option value="">All levels</option><option>info</option><option>warning</option><option>error</option></select>
        <ProjectPicker allowAll value={project} onChange={setProject} /></>}>
        <ErrorNote error={error} />
        {loading && !data ? <Spinner /> : rows.length === 0 ? <Empty title="No matching events" /> : (
          <ul className="divide-y divide-line">{rows.map((a) => (
            <li key={a.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 py-2 text-sm">
              <Badge tone={a.level === "error" ? "bad" : a.level === "warning" ? "warn" : "muted"}>{a.level}</Badge>
              <div className="min-w-0"><p className="break-words">{a.message}</p><p className="font-mono text-[11px] text-muted">{a.event}{a.task_id ? ` · task #${a.task_id}` : ""}</p></div>
              <span className="whitespace-nowrap text-xs text-muted" title={a.created_at}>{timeAgo(a.created_at)}</span>
            </li>
          ))}</ul>
        )}
      </Card>
    </>
  );
}
