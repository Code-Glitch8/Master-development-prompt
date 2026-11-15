import { useState } from "react";
import { AlertCircle, Bot, Download, Flag, Info, Paperclip, Search, User } from "lucide-react";
import { api, qs, type ConversationSummary, type Message } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Button, Card, Empty, ErrorNote, inputCls, Spinner } from "./ui";
import { timeAgo } from "../lib/format";

const KINDS: Record<string, { label: string; icon: JSX.Element; cls: string }> = {
  user_prompt: { label: "Prompt", icon: <User className="h-3.5 w-3.5" />, cls: "border-accent/40 bg-accent/10" },
  ai_response: { label: "AI response", icon: <Bot className="h-3.5 w-3.5" />, cls: "border-ok/40 bg-ok/10" },
  system_event: { label: "System", icon: <Info className="h-3.5 w-3.5" />, cls: "border-line bg-panel2" },
  file_attachment: { label: "Attachment", icon: <Paperclip className="h-3.5 w-3.5" />, cls: "border-info/40 bg-info/10" },
  error: { label: "Error", icon: <AlertCircle className="h-3.5 w-3.5" />, cls: "border-bad/40 bg-bad/10" },
  checkpoint: { label: "Checkpoint", icon: <Flag className="h-3.5 w-3.5" />, cls: "border-warn/40 bg-warn/10" },
};

export function MessageBubble({ m }: { m: Message }) {
  const k = KINDS[m.kind] ?? KINDS.system_event;
  return (
    <div className={`rounded-xl border p-3 ${k.cls}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="inline-flex items-center gap-1 font-medium text-fg">{k.icon}{k.label}</span>
        {m.account_name && <span>{m.provider_name} / {m.account_name}</span>}
        {m.execution_status && <span>· {m.execution_status.replace("_", " ")}</span>}
        <span className="ml-auto" title={m.created_at}>{timeAgo(m.created_at)}</span>
      </div>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words font-sans text-sm">{m.content}</pre>
    </div>
  );
}

export function ConversationsPanel({ projectId }: { projectId?: number }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const convs = useApi(() => api.get<ConversationSummary[]>(`/conversations${qs({ project_id: projectId })}`), [projectId], ["message"]);
  const searching = q.trim() !== "" || kind !== "";
  const results = useApi(
    () => (searching ? api.get<Message[]>(`/conversations/search${qs({ q, kind, project_id: projectId })}`) : Promise.resolve(null)),
    [q, kind, projectId], ["message"],
  );
  const detail = useApi(
    () => (sel !== null ? api.get<{ title: string; messages: Message[] }>(`/conversations/${sel}`) : Promise.resolve(null)),
    [sel], ["message"],
  );

  const exportConv = async (format: "markdown" | "json") => {
    if (sel === null) return;
    const res = await fetch(`/api/conversations/${sel}/export?format=${format}`);
    const text = format === "json" ? JSON.stringify(await res.json(), null, 2) : await res.text();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: format === "json" ? "application/json" : "text/markdown" }));
    a.download = `conversation-${sel}.${format === "json" ? "json" : "md"}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Card title="Conversations">
        <div className="mb-3 space-y-2">
          <div className="relative"><Search className="absolute left-2.5 top-2 h-4 w-4 text-muted" />
            <input aria-label="Search messages" className={`${inputCls} pl-8`} placeholder="Search messages…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <select aria-label="Filter by type" className={inputCls} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All message types</option>
            {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        <ErrorNote error={convs.error} />
        {searching ? (
          results.loading && !results.data ? <Spinner /> : results.data && results.data.length === 0 ? <Empty title="No matching messages" /> : (
            <ul className="space-y-2">{results.data?.map((m) => (
              <li key={m.id}><button className="w-full rounded-lg border border-line p-2 text-left text-xs hover:bg-panel2" onClick={() => { setSel(m.conversation_id); setQ(""); setKind(""); }}>
                <span className="font-medium">{KINDS[m.kind]?.label ?? m.kind}</span> · task #{m.task_id}<p className="mt-1 line-clamp-2 text-muted">{m.content}</p></button></li>
            ))}</ul>
          )
        ) : convs.loading && !convs.data ? <Spinner /> : convs.data && convs.data.length === 0 ? (
          <Empty title="No conversations yet" hint="Each task gets its own conversation once it is prepared or run." />
        ) : (
          <ul className="space-y-1">{convs.data?.map((c) => (
            <li key={c.id}><button onClick={() => setSel(c.id)} className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${sel === c.id ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
              <p className="truncate font-medium">{c.title || `Task #${c.task_id}`}</p>
              <p className="text-xs text-muted">{c.message_count} messages · {timeAgo(c.last_at)}</p></button></li>
          ))}</ul>
        )}
      </Card>
      <Card title={detail.data?.title || "Conversation"} actions={sel !== null && <>
        <Button small onClick={() => exportConv("markdown")}><Download className="h-3.5 w-3.5" />Markdown</Button>
        <Button small onClick={() => exportConv("json")}><Download className="h-3.5 w-3.5" />JSON</Button></>}>
        {sel === null ? <Empty title="Select a conversation" hint="You'll see everything Project Pilot legitimately recorded: prompts, responses you supplied, system events, errors and checkpoints." />
          : detail.loading && !detail.data ? <Spinner /> : <div className="space-y-3">{detail.data?.messages.map((m) => <MessageBubble key={m.id} m={m} />)}</div>}
      </Card>
    </div>
  );
}
