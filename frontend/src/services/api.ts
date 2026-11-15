export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { message?: string })?.message ?? `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (form) init.body = form;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, "Cannot reach the Project Pilot service. Is it running (run.bat)?");
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const d = (data as { detail?: unknown })?.detail;
    throw new ApiError(res.status, d ?? data);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b ?? {}),
  del: <T = void>(p: string) => request<T>("DELETE", p),
  upload: <T>(p: string, form: FormData) => request<T>("POST", p, undefined, form),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") u.set(k, String(v)); });
  const s = u.toString();
  return s ? `?${s}` : "";
}

// ---- types (mirror backend serializers) ----
export interface Inventory {
  file_count: number; total_bytes: number; languages: Record<string, number>; frameworks: string[];
  project_types: string[]; package_managers: string[]; dependencies: Record<string, string[]>;
  config_files: string[]; doc_files: string[];
  git: { is_repo: boolean; branch?: string; dirty_files?: number; clean?: boolean; last_commit?: string };
  _category_accounts?: Record<string, number>;
}
export interface Project {
  id: number; name: string; slug: string; directory: string; description: string; source_type: string;
  plan_status: string; auto_approve_plan: boolean; instructions: string; created_at: string; updated_at: string;
  task_counts: Record<string, number>; task_total: number; task_done: number; progress: number;
  inventory: Inventory; import_warnings: string[]; account_ids?: number[];
}
export interface Task {
  id: number; project_id: number; key: string; title: string; description: string; category: string;
  priority: number; position: number; status: string; run_state: string; assigned_account_id: number | null;
  required_capability: string; relevant_files: string[]; expected_output: string; error: string;
  progress: number; retry_count: number; max_retries: number; depends_on: number[];
  next_attempt_at: string | null; approved: boolean; created_at: string; completed_at: string | null;
  input_prompt?: string; actual_output?: string;
  executions?: { id: number; attempt: number; mode: string; outcome: string; error: string; started_at: string; finished_at: string | null }[];
}
export interface Account {
  id: number; provider_key: string; provider_name: string; provider_url: string; display_name: string;
  connection_type: string; connection_status: string; execution_mode: string; execution_mode_label: string;
  permitted_methods: string[]; capabilities: string[]; enabled: boolean; notes: string;
  last_verified_at: string | null; unavailable_reason: string; assigned_project_ids: number[];
  config: { model?: string; api_key_env?: string }; has_credential: boolean; created_at: string;
}
export interface Provider { id: number; key: string; name: string; website_url: string; capabilities: string[]; supported_modes: string[]; notes: string }
export interface Message {
  id: number; conversation_id: number; kind: string; content: string; provider_name: string; account_id: number | null;
  account_name: string; task_id: number | null; execution_status: string; meta: Record<string, unknown>; created_at: string;
}
export interface ConversationSummary { id: number; project_id: number; task_id: number | null; title: string; message_count: number; last_kind: string | null; last_at: string | null }
export interface Checkpoint {
  id: number; project_id: number; task_id: number | null; label: string; reason: string; git_commit: string;
  has_zip: boolean; created_at: string; counts: { tasks: number; completed: number; pending: number; files: number };
}
export interface FileChange {
  id: number; project_id: number; task_id: number | null; path: string; change_type: string; decision: string;
  protected: boolean; summary: string; created_at: string;
}
export interface ActivityItem { id: number; event: string; level: string; message: string; project_id: number | null; task_id: number | null; created_at: string }
export interface EngineStatus {
  state: string; loop_alive: boolean; heartbeat: string | null; current_task_id: number | null;
  running_task_ids: number[]; task_counts: Record<string, number>; ws_clients: number;
}
export interface Dashboard {
  projects: number; executing_projects: number; active_tasks: number; completed_tasks: number; awaiting_input: number;
  failed_tasks: number; accounts_total: number; accounts_enabled: number; accounts_automated: number;
  engine: EngineStatus; current_tasks: Task[]; awaiting_tasks: Task[]; recent_activity: ActivityItem[];
}
export interface ProjectFileRow { path: string; size: number; language: string; is_config: boolean; is_doc: boolean; is_protected: boolean }
export interface Notice { id: number; kind: string; title: string; body: string; read: boolean; created_at: string }
