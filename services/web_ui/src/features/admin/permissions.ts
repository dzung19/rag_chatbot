import { useAuthStore } from "../../stores/authStore";

export type Permission = "conversation.read_any" | "conversation.restore" | "audit.read";

/**
 * Tạm thời: đọc `permissions` trong user nếu có; nếu không, cho phép bật
 * chế độ admin bằng localStorage("dev_admin") = "1" để test giao diện.
 * Đây CHỈ là kiểm soát hiển thị. Backend vẫn phải trả 403.
 */
export function usePermissions(): Set<Permission> {
  const user = useAuthStore((s) => s.user) as { permissions?: Permission[] } | null;
  if (user?.permissions) return new Set(user.permissions);
  if (import.meta.env.DEV && localStorage.getItem("dev_admin") === "1") {
    return new Set(["conversation.read_any", "conversation.restore", "audit.read"]);
  }
  return new Set();
}

export function useHasPermission(p: Permission): boolean {
  return usePermissions().has(p);
}
