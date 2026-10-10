import { NavLink, Outlet, useNavigate } from "react-router";
import { ArrowLeft, History, ScrollText, Shield } from "lucide-react";
import styles from "./Admin.module.css";

export function AdminLayout() {
  const navigate = useNavigate();
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    isActive ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink;

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.headerTitle}>
          <Shield size={20} aria-hidden="true" />
          <span>Quản trị</span>
        </div>
        <button type="button" className={styles.backButton} onClick={() => navigate("/")}>
          <ArrowLeft size={16} aria-hidden="true" /> Quay lại chat
        </button>
      </header>

      <nav className={styles.tabs}>
        <NavLink to="/admin/conversations/deleted" className={linkClass}>
          <History size={16} aria-hidden="true" /> Chat đã xóa
        </NavLink>
        <NavLink to="/admin/audit" className={linkClass}>
          <ScrollText size={16} aria-hidden="true" /> Nhật ký quản trị
        </NavLink>
      </nav>

      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
