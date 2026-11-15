import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, File, Folder, ShieldAlert } from "lucide-react";
import { api, qs, type ProjectFileRow } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Card, Diff, Empty, ErrorNote, Spinner } from "./ui";
import { ChangesPanel } from "./Changes";
import { formatBytes, timeAgo } from "../lib/format";

interface Node { name: string; path: string; children: Map<string, Node>; file?: ProjectFileRow }

export function buildTree(files: ProjectFileRow[]): Node {
  const root: Node = { name: "", path: "", children: new Map() };
  for (const f of files) {
    let cur = root;
    const parts = f.path.split("/");
    parts.forEach((p, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!cur.children.has(p)) cur.children.set(p, { name: p, path, children: new Map() });
      cur = cur.children.get(p)!;
      if (i === parts.length - 1) cur.file = f;
    });
  }
  return root;
}

function TreeNode({ n, depth, open, toggle, onPick, picked }: { n: Node; depth: number; open: Set<string>; toggle: (p: string) => void; onPick: (f: ProjectFileRow) => void; picked: string | null }) {
  const kids = [...n.children.values()].sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name));
  return (
    <>
      {kids.map((k) => k.file ? (
        <button key={k.path} onClick={() => onPick(k.file!)} style={{ paddingLeft: depth * 14 + 8 }}
          className={`flex w-full items-center gap-1.5 rounded py-0.5 pr-2 text-left text-sm hover:bg-panel2 ${picked === k.path ? "bg-accent/15" : ""}`}>
          <File className="h-3.5 w-3.5 shrink-0 text-muted" /><span className="truncate">{k.name}</span>
          {k.file.is_protected && <ShieldAlert className="h-3 w-3 shrink-0 text-warn" aria-label="protected" />}
        </button>
      ) : (
        <div key={k.path}>
          <button onClick={() => toggle(k.path)} style={{ paddingLeft: depth * 14 + 4 }} className="flex w-full items-center gap-1 rounded py-0.5 text-left text-sm hover:bg-panel2">
            {open.has(k.path) ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}<Folder className="h-3.5 w-3.5 text-accent" />{k.name}
          </button>
          {open.has(k.path) && <TreeNode n={k} depth={depth + 1} open={open} toggle={toggle} onPick={onPick} picked={picked} />}
        </div>
      ))}
    </>
  );
}

export function FileExplorerPanel({ projectId }: { projectId: number }) {
  const files = useApi(() => api.get<ProjectFileRow[]>(`/projects/${projectId}/files`), [projectId], ["file_changes", "checkpoint"]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<ProjectFileRow | null>(null);
  const [tab, setTab] = useState<"preview" | "history">("preview");
  const tree = useMemo(() => buildTree(files.data ?? []), [files.data]);
  const toggle = (p: string) => setOpen((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });

  const content = useApi(
    () => (picked && !picked.is_protected ? api.get<{ content: string }>(`/projects/${projectId}/file${qs({ path: picked.path })}`).catch((e) => ({ content: `(${e.message})` })) : Promise.resolve(null)),
    [picked?.path, projectId],
  );
  const history = useApi(() => api.get<{ hash: string; short: string; date: string; subject: string }[]>(`/projects/${projectId}/history${qs({ path: picked?.path, limit: 50 })}`), [projectId, picked?.path], ["checkpoint", "file_changes"]);
  const [diff, setDiff] = useState<{ hash: string; text: string } | null>(null);
  const showDiff = async (hash: string) => {
    if (diff?.hash === hash) return setDiff(null);
    const res = await fetch(`/api/projects/${projectId}/history/diff${qs({ a: `${hash}~1`, b: hash, path: picked?.path })}`);
    setDiff({ hash, text: await res.text() });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card title={`Project files${files.data ? ` (${files.data.length})` : ""}`}>
          <ErrorNote error={files.error} />
          {files.loading && !files.data ? <Spinner /> : files.data?.length === 0 ? <Empty title="No files" /> : (
            <div className="max-h-[520px] overflow-auto"><TreeNode n={tree} depth={0} open={open} toggle={toggle} onPick={setPicked} picked={picked?.path ?? null} /></div>
          )}
        </Card>
        <Card title={picked ? picked.path : "Select a file"} actions={picked && (
          <div className="flex gap-1 text-xs">
            {(["preview", "history"] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={`rounded px-2 py-1 ${tab === t ? "bg-accent/20 text-accent" : "hover:bg-panel2"}`}>{t === "preview" ? "Preview" : "History"}</button>)}
          </div>
        )}>
          {!picked ? <Empty title="Pick a file to preview it and see its change history" /> : tab === "preview" ? (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2 text-xs text-muted"><span>{formatBytes(picked.size)}</span>{picked.language && <Badge>{picked.language}</Badge>}{picked.is_config && <Badge tone="info">config</Badge>}{picked.is_protected && <Badge tone="warn">protected · never shown or overwritten</Badge>}</div>
              {picked.is_protected ? <p className="text-sm text-muted">Credentials, keys, environment files and databases are never displayed or modified.</p>
                : content.loading ? <Spinner /> : <pre className="max-h-[460px] overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-xs leading-5">{content.data?.content}</pre>}
            </div>
          ) : (
            history.data && history.data.length === 0 ? <Empty title="No history for this file" /> : (
              <ul className="divide-y divide-line">{history.data?.map((h) => (
                <li key={h.hash} className="py-2"><button className="flex w-full items-center gap-3 text-left text-sm" onClick={() => showDiff(h.hash)}>
                  <code className="text-xs text-info">{h.short}</code><span className="flex-1 truncate">{h.subject}</span><span className="text-xs text-muted">{timeAgo(h.date)}</span></button>
                  {diff?.hash === h.hash && <div className="mt-2"><Diff text={diff.text} /></div>}</li>
              ))}</ul>
            )
          )}
        </Card>
      </div>
      <ChangesPanel projectId={projectId} />
    </div>
  );
}
