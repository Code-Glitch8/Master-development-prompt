import { useState } from "react";
import { Bot, CheckCircle2, ExternalLink, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { api, type Account, type Provider } from "../services/api";
import { useApi } from "../hooks/useApi";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, ModeBadge, Modal, Spinner } from "../components/ui";
import { useProjects } from "../components/ProjectPicker";
import { timeAgo } from "../lib/format";

const CAPS = ["analysis", "planning", "code", "documentation", "review", "testing"];

function AccountForm({ providers, types, initial, onClose, onDone }: { providers: Provider[]; types: Record<string, string>; initial?: Account; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({
    provider_key: initial?.provider_key ?? providers[0]?.key ?? "claude", display_name: initial?.display_name ?? "",
    connection_type: initial?.connection_type ?? "web_manual", notes: initial?.notes ?? "", model: initial?.config.model ?? "",
    api_key: "", api_key_env: initial?.config.api_key_env ?? "", capabilities: initial?.capabilities ?? CAPS, enabled: initial?.enabled ?? true,
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prov = providers.find((p) => p.key === f.provider_key);
  const apiCapable = f.provider_key !== "other";

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = { display_name: f.display_name, connection_type: f.connection_type, notes: f.notes, capabilities: f.capabilities, enabled: f.enabled };
      if (f.connection_type === "api_key") { body.model = f.model; body.api_key_env = f.api_key_env; if (f.api_key) body.api_key = f.api_key; }
      if (initial) await api.patch(`/accounts/${initial.id}`, body);
      else await api.post("/accounts", { ...body, provider_key: f.provider_key });
      onDone(); onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  return (
    <Modal title={initial ? "Edit account" : "Add AI account"} onClose={onClose}>
      <div className="space-y-3">
        <ErrorNote error={err} />
        <Field label="Provider"><select className={inputCls} disabled={!!initial} value={f.provider_key} onChange={(e) => setF({ ...f, provider_key: e.target.value, connection_type: "web_manual" })}>{providers.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></Field>
        <Field label="Display name"><input autoFocus className={inputCls} placeholder="e.g. Claude Account 1" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Connection type" hint={types[f.connection_type]}>
          <select className={inputCls} value={f.connection_type} onChange={(e) => setF({ ...f, connection_type: e.target.value })}>
            <option value="web_manual">Official website (user-assisted)</option><option value="manual_only">Manual only</option>
            {apiCapable && <option value="api_key">Official API with my own key (automated)</option>}
          </select>
        </Field>
        {f.connection_type === "api_key" && (
          <div className="space-y-3 rounded-lg border border-line bg-panel2 p-3">
            <p className="flex gap-2 text-xs text-muted"><ShieldCheck className="h-4 w-4 shrink-0 text-ok" />Optional. Your key is stored in the Windows credential store, never in the database, exports or logs. Without a model and key this account stays user-assisted.</p>
            <Field label="Model name" hint="Enter the exact model ID from your provider's documentation."><input className={inputCls} value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></Field>
            <Field label={initial?.has_credential ? "API key (leave blank to keep the stored key)" : "API key"}><input type="password" autoComplete="off" className={inputCls} value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></Field>
            <Field label="…or environment variable name holding the key"><input className={`${inputCls} font-mono`} placeholder="MY_PROVIDER_API_KEY" value={f.api_key_env} onChange={(e) => setF({ ...f, api_key_env: e.target.value })} /></Field>
          </div>
        )}
        <Field label="Capabilities"><div className="flex flex-wrap gap-3">{CAPS.map((c) => (
          <label key={c} className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={f.capabilities.includes(c)} onChange={(e) => setF({ ...f, capabilities: e.target.checked ? [...f.capabilities, c] : f.capabilities.filter((x) => x !== c) })} />{c}</label>
        ))}</div></Field>
        <Field label="Notes"><textarea rows={2} className={inputCls} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <p className="text-xs text-muted">{prov?.notes}</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />Enabled</label>
        <Button variant="primary" busy={busy} disabled={!f.display_name.trim()} onClick={save}>{initial ? "Save changes" : "Add account"}</Button>
      </div>
    </Modal>
  );
}

export function Accounts() {
  const accounts = useApi(() => api.get<Account[]>("/accounts"), []);
  const providers = useApi(() => api.get<Provider[]>("/providers"), []);
  const types = useApi(() => api.get<Record<string, string>>("/connection-types"), []);
  const projects = useProjects();
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<Record<number, string>>({});

  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); void accounts.reload(true); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } };
  const verify = (a: Account) => run(async () => { const r = await api.post<{ message: string }>(`/accounts/${a.id}/verify`); setNote((n) => ({ ...n, [a.id]: r.message })); });
  const projName = (id: number) => projects.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  const grouped = (providers.data ?? []).map((p) => ({ p, list: (accounts.data ?? []).filter((a) => a.provider_key === p.key) })).filter((g) => g.list.length);

  return (
    <>
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold">AI accounts</h1>
        <Button variant="primary" onClick={() => setEditing("new")} disabled={!providers.data}><Plus className="h-4 w-4" />Add account</Button></div>
      <Card><p className="text-sm text-muted">Each account shows honestly how it can be used. <strong className="text-fg">Project Pilot never logs in to consumer websites, reads cookies or sessions, rotates accounts, or works around usage limits.</strong> Web accounts are used by you: Project Pilot prepares the prompt and records the response you paste back.</p></Card>
      <ErrorNote error={err ?? accounts.error} />
      {accounts.loading && !accounts.data ? <Spinner /> : grouped.length === 0 ? (
        <Card><Empty icon={<Bot className="h-7 w-7" />} title="No accounts yet" hint="Add one record per provider account you use (for example Claude Account 1, Claude Account 2, ChatGPT Account 1). No password or API key is required." /></Card>
      ) : grouped.map(({ p, list }) => (
        <Card key={p.key} title={<span className="flex items-center gap-2">{p.name}<Badge>{list.length}</Badge></span>} actions={p.website_url ? <a href={p.website_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline"><ExternalLink className="h-3 w-3" />Official site</a> : null}>
          <ul className="divide-y divide-line">{list.map((a) => (
            <li key={a.id} className="space-y-2 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">{a.display_name}</p><ModeBadge mode={a.execution_mode} />
                {!a.enabled && <Badge tone="bad">disabled</Badge>}
                {a.connection_type === "api_key" && <Badge tone={a.has_credential ? "ok" : "warn"}>{a.has_credential ? "key found" : "no key"}</Badge>}
                {a.unavailable_reason && <Badge tone="bad" title={a.unavailable_reason}>unavailable</Badge>}
                <span className="ml-auto flex gap-1.5">
                  <Button small onClick={() => verify(a)}><CheckCircle2 className="h-3.5 w-3.5" />Verify</Button>
                  <Button small onClick={() => setEditing(a)}><Pencil className="h-3.5 w-3.5" />Edit</Button>
                  <Button small variant="danger" aria-label={`Delete ${a.display_name}`} onClick={() => { if (window.confirm(`Remove ${a.display_name}? Its stored key (if any) is deleted.`)) void run(() => api.del(`/accounts/${a.id}`)); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                </span>
              </div>
              <div className="grid gap-x-6 gap-y-1 text-xs text-muted sm:grid-cols-2 lg:grid-cols-4">
                <span>Connection: <span className="text-fg">{a.connection_type.replace("_", " ")}</span></span>
                <span>Status: <span className="text-fg">{a.connection_status}</span></span>
                <span>Last verified: <span className="text-fg">{timeAgo(a.last_verified_at)}</span></span>
                <span>Projects: <span className="text-fg">{a.assigned_project_ids.length ? a.assigned_project_ids.map(projName).join(", ") : "none"}</span></span>
              </div>
              <div className="flex flex-wrap gap-1">{a.capabilities.map((c) => <Badge key={c}>{c}</Badge>)}</div>
              {a.notes && <p className="text-xs text-muted">{a.notes}</p>}
              {note[a.id] && <p className="text-xs text-info">{note[a.id]}</p>}
              {a.unavailable_reason && <p className="text-xs text-bad">{a.unavailable_reason}</p>}
            </li>
          ))}</ul>
        </Card>
      ))}
      {editing && providers.data && types.data && (
        <AccountForm providers={providers.data} types={types.data} initial={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} onDone={() => void accounts.reload(true)} />
      )}
    </>
  );
}
