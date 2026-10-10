import { NavLink } from "react-router";
import { Shield } from "lucide-react";
import { useHasPermission } from "../permissions";

/** Đặt trong Sidebar. Chỉ hiển thị khi có quyền. */
export function AdminNavItem({ className }: { className?: string }) {
  if (!useHasPermission("conversation.read_any")) return null;
  return (
    <NavLink to="/admin/conversations/deleted" className={className}>
      <Shield size={16} aria-hidden="true" /> Quản trị
    </NavLink>
  );
}
