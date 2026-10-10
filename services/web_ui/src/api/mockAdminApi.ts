import { request } from "./client";
import type {
  AuditLogEntry,
  DeletedConversation,
} from "../features/admin/types";

interface RestoreResponse {
  id: string;
  owner_id: string;
  restored: boolean;
}

async function readResponse<T>(
  response: Response,
): Promise<T> {
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;

    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Giữ thông báo mặc định nếu response không phải JSON.
    }

    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export async function fetchDeletedConversations(): Promise<
  DeletedConversation[]
> {
  const response = await request(
    "/admin/conversations/deleted?limit=100&offset=0",
    {
      method: "GET",
    },
  );

  return readResponse<DeletedConversation[]>(response);
}

export async function restoreConversation(
  id: string,
  reason: string,
): Promise<void> {
  const response = await request(
    `/admin/conversations/${encodeURIComponent(id)}/restore`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        reason: reason.trim(),
      }),
    },
  );

  await readResponse<RestoreResponse>(response);
}

export async function fetchAuditLogs(): Promise<
  AuditLogEntry[]
> {
  const response = await request(
    "/admin/audit?limit=100&offset=0",
    {
      method: "GET",
    },
  );

  return readResponse<AuditLogEntry[]>(response);
}