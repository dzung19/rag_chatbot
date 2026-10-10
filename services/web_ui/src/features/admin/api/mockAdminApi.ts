/**
 * Mock API cho UI quản trị. Thay bằng apiRequest() khi backend sẵn sàng:
 *   GET  /admin/conversations/deleted
 *   POST /admin/conversations/{id}/restore  { reason }
 *   GET  /admin/audit
 */
import type { AuditLogEntry, DeletedConversation } from "../types";

const daysAgo = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();

let deleted: DeletedConversation[] = [
  { id: "c-001", title: "So sánh BL 830753", owner_id: "u-01", owner_name: "Nguyễn Văn A", deleted_at: daysAgo(28), message_count: 6 },
  { id: "c-002", title: "What is the SHIPPER?", owner_id: "u-02", owner_name: "Trần Thị B", deleted_at: daysAgo(15), message_count: 2 },
  { id: "c-003", title: "Tóm tắt quy định R40-25", owner_id: "u-01", owner_name: "Nguyễn Văn A", deleted_at: daysAgo(3), message_count: 10 },
  { id: "c-004", title: "Kiểm tra Packing List", owner_id: "u-03", owner_name: "Lê Văn C", deleted_at: daysAgo(1), message_count: 4 },
];

let audit: AuditLogEntry[] = [];

const delay = (ms = 400) => new Promise((r) => setTimeout(r, ms));

export async function fetchDeletedConversations(): Promise<DeletedConversation[]> {
  await delay();
  return [...deleted];
}

export async function restoreConversation(id: string, reason: string, actorId: string): Promise<void> {
  await delay();
  const target = deleted.find((c) => c.id === id);
  if (!target) throw new Error("Không tìm thấy cuộc trò chuyện (có thể đã bị xóa vĩnh viễn).");
  if (!reason.trim()) throw new Error("Bắt buộc nhập lý do.");

  deleted = deleted.filter((c) => c.id !== id);
  audit = [
    {
      id: crypto.randomUUID(),
      actor_id: actorId,
      action: "conversation.restore",
      target_id: target.id,
      target_title: target.title,
      target_owner_id: target.owner_id,
      reason: reason.trim(),
      created_at: new Date().toISOString(),
    },
    ...audit,
  ];
}

export async function fetchAuditLogs(): Promise<AuditLogEntry[]> {
  await delay();
  return [...audit];
}
