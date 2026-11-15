import { useEffect } from "react";
import { api, type Project } from "../services/api";
import { useApi } from "../hooks/useApi";
import { inputCls } from "./ui";

export function useProjects() {
  return useApi(() => api.get<Project[]>("/projects"), [], ["project"]);
}

/** Select a project. When `allowAll` is false the first project is auto-selected. */
export function ProjectPicker({ value, onChange, allowAll }: { value: number | null; onChange: (id: number | null) => void; allowAll?: boolean }) {
  const { data } = useProjects();
  useEffect(() => { if (!allowAll && value === null && data && data.length) onChange(data[0].id); }, [allowAll, value, data, onChange]);
  return (
    <select aria-label="Project" className={`${inputCls} max-w-xs`} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      {allowAll && <option value="">All projects</option>}
      {data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}
