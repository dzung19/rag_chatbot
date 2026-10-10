export interface DeletedConversation {
  id: string;
  title: string;
  owner_id: string;
  owner_name: string;
  deleted_at: string;
  message_count: number;
}

export interface AuditLogEntry {
  id: string;
  actor_id: string;
  action: "conversation.restore";
  target_id: string;
  target_title: string;
  target_owner_id: string;
  reason: string;
  created_at: string;
}

export const RETENTION_DAYS = 30;
