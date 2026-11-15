import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Activity, Bell, Bot, CheckSquare, FileText, FolderGit2, Gauge, ListChecks, MessageSquare, Moon, Settings as Cog, Sun, Flag, Wifi, WifiOff, Menu } from "lucide-react";
import { useTheme } from "../hooks/useTheme";
import { useLiveStatus } from "../hooks/useLiveStatus";
import { useApi } from "../hooks/useApi";
import { api, type EngineStatus, type Notice } from "../services/api";
import { live } from "../services/ws";
import { Badge } from "../components/ui";
import { timeAgo } from "../lib/format";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/projects", label: "Projects", icon: FolderGit2 },
  { to: "/accounts", label: "AI Accounts", icon: Bot },
  { to: "/queue", label: "Task Queue", icon: ListChecks },
  { to: "/conversations", label: "Conversations", icon: MessageSquare },
  { to: "/checkpoints", label: "Checkpoints", icon: Flag },
  { to: "/files", label: "File Explorer", icon: FileText },
  { to: "/activity", label: "Activity Logs", icon: Activity },
  { to: "/settings", label: "Settings", icon: Cog },
];

const ENGINE_TONE: Record<string, "ok" | "warn" | "bad"> = { running: "ok", paused: "warn", stopped: "bad" };

export function AppLayout() {
  const { theme, toggle } = useTheme();
  const status = useLiveStatus();
  const [menu, setMenu] = useState(false);
  const [bell, setBell] = useState(false);
  const engine = useApi(() => api.get<EngineStatus>("/engine/status"), [], ["engine", "task"]);
  const notes = useApi(() => api.get<Notice[]>("/notifications"), [], ["notification"]);
  const unread = notes.data?.filter((n) => !n.read).length ?? 0;
  const e = engine.data;

  useEffect(() => { live.start(); return () => live.stop(); }, []);

  return (
    <div className="flex h-full">
      {menu && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setMenu(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-line bg-panel transition-transform lg:static lg:translate-x-0 ${menu ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex h-14 items-center gap-2 border-b border-line px-4">
          <div className="grid h-7 w-7 place-items-center rounded-lg bg-accent text-white"><CheckSquare className="h-4 w-4" /></div>
          <div className="leading-tight"><p className="text-sm font-semibold">Project Pilot</p><p className="text-[10px] text-muted">Multi-AI project manager</p></div>
        </div>
        <nav className="space-y-0.5 p-2" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setMenu(false)}
              className={({ isActive }) => `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${isActive ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-panel2 hover:text-fg"}`}>
              <Icon className="h-4 w-4" />{label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
          <button className="rounded p-1.5 hover:bg-panel2 lg:hidden" aria-label="Open menu" onClick={() => setMenu(true)}><Menu className="h-5 w-5" /></button>
          <div className="flex flex-1 items-center gap-2 text-xs">
            <Badge tone={e ? ENGINE_TONE[e.state] ?? "muted" : "muted"} title="The background engine runs on this computer independently of this page">
              Engine: {e ? e.state : "unknown"}
            </Badge>
            {e && e.running_task_ids.length > 0 && <Badge tone="accent">{e.running_task_ids.length} processing</Badge>}
          </div>
          <span className={`inline-flex items-center gap-1 text-xs ${status === "open" ? "text-ok" : "text-warn"}`} role="status">
            {status === "open" ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {status === "open" ? "Live" : status === "closed" ? "Offline" : "Reconnecting…"}
          </span>
          <div className="relative">
            <button aria-label="Notifications" className="relative rounded p-1.5 hover:bg-panel2" onClick={() => setBell((b) => !b)}>
              <Bell className="h-4 w-4" />{unread > 0 && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-bad px-1 text-[10px] font-bold text-white">{unread}</span>}
            </button>
            {bell && (
              <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-line bg-panel shadow-2xl">
                <div className="flex items-center justify-between border-b border-line px-3 py-2 text-sm font-medium">Notifications
                  <button className="text-xs text-accent" onClick={async () => { await api.post("/notifications/read-all"); void notes.reload(true); }}>Mark all read</button></div>
                <ul className="max-h-80 divide-y divide-line overflow-auto">
                  {notes.data?.length ? notes.data.map((n) => (
                    <li key={n.id} className={`px-3 py-2 text-xs ${n.read ? "text-muted" : ""}`}><p className="font-medium">{n.title}</p><p className="text-muted">{n.body}</p><p className="text-[10px] text-muted">{timeAgo(n.created_at)}</p></li>
                  )) : <li className="px-3 py-6 text-center text-xs text-muted">Nothing yet</li>}
                </ul>
              </div>
            )}
          </div>
          <button aria-label="Toggle theme" className="rounded p-1.5 hover:bg-panel2" onClick={toggle}>{theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</button>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6"><div className="mx-auto min-w-0 max-w-7xl space-y-4"><Outlet /></div></main>
      </div>
    </div>
  );
}
