import type { ReactNode } from "react";

import { useAuthStore } from "../../stores/authStore";

import styles from "./AuthGate.module.css";
import { LoginPage } from "../login/LoginPage";

type AuthGateProps = {
  children: ReactNode;
};

export function AuthGate({ children }: AuthGateProps) {
  const isHydrated = useAuthStore((state) => state.isHydrated);
  const isAuthenticated = useAuthStore(
    (state) => state.isAuthenticated,
  );

  if (!isHydrated) {
    return (
      <main className={styles.loading}>
        <div className={styles.spinner} aria-hidden="true" />
        <p>Restoring session...</p>
      </main>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <>
      {children}
    </>
  );
}
