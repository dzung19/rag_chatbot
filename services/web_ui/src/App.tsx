import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { ChatView } from "./features/chat/ChatView";
import { DocumentsView } from "./features/documents/DocumentsView";
// import { LogsView } from "./features/logs/LogsView";
import { SettingsView } from "./features/settings/SettingsView";
import { AuthGate } from "./features/auth/AuthGate";
import { AdminLayout, AuditLogPage, DeletedConversationsPage, RequirePermission } from "./features/admin";

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AuthGate>
        <AppShell>
          <Routes>
            <Route
              path="/admin"
              element={
                <RequirePermission permission="conversation.read_any">
                  <AdminLayout />
                </RequirePermission>
              }>
              <Route index element={<Navigate to="conversations/deleted" replace />} />
              <Route path="conversations/deleted" element={<DeletedConversationsPage />} />
              <Route path="audit" element={<AuditLogPage />} />
            </Route>
            <Route path="/" element={<ChatView />} />
            <Route path="/c/:id" element={<ChatView />} />
            <Route path="/documents" element={<DocumentsView />} />
            {/* <Route path="/logs" element={<LogsView />} /> */}
            <Route path="/settings" element={<SettingsView />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </AuthGate>
    </BrowserRouter>
  );
};

export default App;
