import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { FolderGit2, Plus } from "lucide-react";
import { api, type Project } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, Modal, Progress, Spinner } from "../components/ui";
import { formatBytes, pct, timeAgo } from "../lib/format";

type Kind = "empty" | "zip" | "folder" | "git" | "files";
const KINDS: [Kind, string][] = [["zip", "ZIP archive"], ["folder", "Local folder"], ["git", "Git repository"], ["files", "Files"], ["empty", "Empty project"]];

function NewProject({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [kind, setKind] = useState<Kind>("zip");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ok = kind === "empty" ? !!name.trim() : kind === "folder" ? !!path.trim() : kind === "git" ? !!url.trim() : files.length > 0 && (kind === "files" || files.length === 1);

  const go = async () => {
    setBusy(true); setErr(null);
    try {
      let p: Project;
      if (kind === "empty") p = await api.post<Project>("/projects", { name, description: desc });
      else if (kind === "folder") p = await api.post<Project>("/projects/import/folder", { path, name: name || undefined, description: desc });
      else if (kind === "git") p = await api.post<Project>("/projects/import/git", { url, name: name || undefined, branch: branch || undefined, description: desc });
      else {
        const f = new FormData();
        if (name) f.set("name", name);
        if (kind === "zip") f.set("file", files[0]); else files.forEach((x) => f.append("files", x));
        p = await api.upload<Project>(kind === "zip" ? "/projects/import/zip" : "/projects/import/files", f);
      }
      nav(`/projects/${p.id}`);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  return (
    <Modal title="New project" onClose={onClose}>
      <div className="space-y-3">
        <div role="tablist" className="flex flex-wrap gap-1">{KINDS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={kind === k} onClick={() => setKind(k)} className={`rounded-lg border px-3 py-1 text-sm ${kind === k ? "border-accent bg-accent/15 text-accent" : "border-line hover:bg-panel2"}`}>{l}</button>
        ))}</div>
        <ErrorNote error={err} />
        <Field label={kind === "empty" ? "Project name" : "Project name (optional)"}><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        {kind === "zip" && <Field label="ZIP archive" hint="Extracted safely: path traversal, symlinks, oversized and nested archives are handled. Nothing inside is ever executed."><input type="file" accept=".zip" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} className="text-sm" /></Field>}
        {kind === "files" && <Field label="Files"><input type="file" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} className="text-sm" /></Field>}
        {kind === "folder" && <Field label="Folder path on this computer" hint="The folder is copied into Project Pilot; the original is never modified."><input className={`${inputCls} font-mono`} placeholder="C:\Projects\my-app" value={path} onChange={(e) => setPath(e.target.value)} /></Field>}
        {kind === "git" && <>
          <Field label="Repository URL" hint="https://, ssh:// or git@host:path. Uses your own Git credentials; Project Pilot never asks for them."><input className={`${inputCls} font-mono`} placeholder="https://github.com/you/repo.git" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
          <Field label="Branch (optional)"><input className={inputCls} value={branch} onChange={(e) => setBranch(e.target.value)} /></Field></>}
        <Field label="Description (optional)"><input className={inputCls} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        <Button variant="primary" busy={busy} disabled={!ok} onClick={go}>{kind === "empty" ? "Create project" : "Import project"}</Button>
      </div>
    </Modal>
  );
}

export function Projects() {
  const { data, error, loading } = useApi(() => api.get<Project[]>("/projects"), [], ["project", "task"]);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold">Projects</h1><Button variant="primary" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />New project</Button></div>
      <ErrorNote error={error} />
      {loading && !data ? <Spinner /> : data && data.length === 0 ? (
        <Card><Empty icon={<FolderGit2 className="h-7 w-7" />} title="No projects yet" hint="Import a ZIP, folder or Git repository, or start an empty project." /></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{data?.map((p) => (
          <Link key={p.id} to={`/projects/${p.id}`} className="block rounded-xl border border-line bg-panel p-4 transition hover:border-accent fade-in">
            <div className="flex items-start justify-between gap-2"><h2 className="truncate font-semibold">{p.name}</h2><Badge tone={p.plan_status === "executing" ? "accent" : p.plan_status === "completed" ? "ok" : "muted"}>{p.plan_status}</Badge></div>
            <p className="mt-1 line-clamp-2 min-h-[2.5rem] text-sm text-muted">{p.description || p.directory}</p>
            <div className="mt-3 flex flex-wrap gap-1">{Object.keys(p.inventory.languages ?? {}).slice(0, 4).map((l) => <Badge key={l}>{l}</Badge>)}</div>
            <div className="mt-3"><Progress value={p.progress} /><p className="mt-1 text-xs text-muted">{p.task_done}/{p.task_total} tasks · {pct(p.progress)} · {p.inventory.file_count ?? 0} files ({formatBytes(p.inventory.total_bytes ?? 0)}) · {timeAgo(p.updated_at)}</p></div>
          </Link>
        ))}</div>
      )}
      {adding && <NewProject onClose={() => setAdding(false)} />}
    </>
  );
}
