import { useState } from "react";
import { Card, Empty } from "../components/ui";
import { ProjectPicker } from "../components/ProjectPicker";
import { TaskPanel } from "../components/Tasks";
import { ConversationsPanel } from "../components/Conversations";
import { CheckpointsPanel } from "../components/Checkpoints";
import { FileExplorerPanel } from "../components/Files";

function Page({ title, children, picker }: { title: string; children: React.ReactNode; picker: React.ReactNode }) {
  return (<><div className="flex flex-wrap items-center justify-between gap-2"><h1 className="text-xl font-semibold">{title}</h1>{picker}</div>{children}</>);
}

export function QueuePage() {
  const [pid, setPid] = useState<number | null>(null);
  return <Page title="Task queue" picker={<ProjectPicker allowAll value={pid} onChange={setPid} />}><TaskPanel projectId={pid ?? undefined} /></Page>;
}
export function ConversationsPage() {
  const [pid, setPid] = useState<number | null>(null);
  return <Page title="Conversations" picker={<ProjectPicker allowAll value={pid} onChange={setPid} />}><ConversationsPanel projectId={pid ?? undefined} /></Page>;
}
export function CheckpointsPage() {
  const [pid, setPid] = useState<number | null>(null);
  return <Page title="Checkpoints" picker={<ProjectPicker value={pid} onChange={setPid} />}>{pid === null ? <Card><Empty title="No project selected" hint="Create or import a project first." /></Card> : <CheckpointsPanel projectId={pid} />}</Page>;
}
export function FileExplorerPage() {
  const [pid, setPid] = useState<number | null>(null);
  return <Page title="File explorer" picker={<ProjectPicker value={pid} onChange={setPid} />}>{pid === null ? <Card><Empty title="No project selected" hint="Create or import a project first." /></Card> : <FileExplorerPanel projectId={pid} />}</Page>;
}
