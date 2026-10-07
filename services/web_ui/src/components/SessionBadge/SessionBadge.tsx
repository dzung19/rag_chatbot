import { LogOut, UserRound } from "lucide-react";


import styles from "./SessionBadge.module.css";
import { useAuthStore } from "../../stores/authStore";

export function SessionBadge() {
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  if (!user) {
    return null;
  }

  return (
    <aside className={styles.badge} aria-label="Signed-in user">
      <UserRound size={18} aria-hidden="true" />

      <div className={styles.userText}>
        <strong>{user.name}</strong>
        <span>{user.email}</span>
      </div>

      <button
        type="button"
        className={styles.logoutButton}
        aria-label="Sign out"
        title="Sign out"
        onClick={logout}
      >
        <LogOut size={18} aria-hidden="true" />
      </button>
    </aside>
  );
}
