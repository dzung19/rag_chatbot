import type { ReactNode } from "react";
import { ShieldAlert } from "lucide-react";
import { useHasPermission, type Permission } from "../permissions";
import styles from "./Admin.module.css";

export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  if (!useHasPermission(permission)) {
    return (
      <div className={styles.forbidden}>
        <ShieldAlert size={40} aria-hidden="true" />
        <h2>403 - Không có quyền truy cập</h2>
        <p>Trang này chỉ dành cho quản trị viên.</p>
      </div>
    );
  }
  return <>{children}</>;
}
