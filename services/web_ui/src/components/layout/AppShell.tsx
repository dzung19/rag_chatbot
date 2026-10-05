import React, { useEffect } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { MobileDrawer } from "./MobileDrawer";
import { ApiKeyModal } from "../../features/auth/ApiKeyModal";
import { ToastContainer } from "../ui/ToastContainer";
import { useHealthStore } from "../../stores/healthStore";
import { useUiStore } from "../../stores/uiStore";
import styles from "./AppShell.module.css";

interface AppShellProps {
  children: React.ReactNode;
}

export const AppShell: React.FC<AppShellProps> = ({ children }) => {
  const { startPolling } = useHealthStore();
  const { theme } = useUiStore();

  useEffect(() => {
    // Apply current theme to HTML tag
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    // Start health check polling
    const cleanup = startPolling(30000);
    return cleanup;
  }, [startPolling]);

  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.mainArea}>
        <TopBar />
        <main className={styles.content}>{children}</main>
      </div>
      <MobileDrawer />
      <ApiKeyModal />
      <ToastContainer />
    </div>
  );
};
