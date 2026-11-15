import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./layouts/AppLayout";
import { Dashboard } from "./pages/Dashboard";
import { Projects } from "./pages/Projects";
import { ProjectDetail } from "./pages/ProjectDetail";
import { Accounts } from "./pages/Accounts";
import { Activity } from "./pages/Activity";
import { Settings } from "./pages/Settings";
import { CheckpointsPage, ConversationsPage, FileExplorerPage, QueuePage } from "./pages/Pickers";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="projects" element={<Projects />} />
          <Route path="projects/:id" element={<ProjectDetail />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="queue" element={<QueuePage />} />
          <Route path="conversations" element={<ConversationsPage />} />
          <Route path="checkpoints" element={<CheckpointsPage />} />
          <Route path="files" element={<FileExplorerPage />} />
          <Route path="activity" element={<Activity />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
