import { deleteRequest, getJSON, patchJSON, postJSON, uploadFile } from "./client";
import type {
  ApiConversationDetail,
  ApiConversationSummary,
  DocumentsResponse,
  HealthResponse,
  LogsQueryParams,
  LogsResponse,
  SharePointAuthUrlResponse,
} from "../types/api";

export async function fetchHealth(): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/health");
}

export async function fetchDocuments(): Promise<DocumentsResponse> {
  return getJSON<DocumentsResponse>("/documents");
}

export async function uploadDocumentFile(file: File): Promise<{ message?: string; document_id?: string }> {
  return uploadFile("/documents/upload", file);
}

export async function deleteDocument(documentId: string): Promise<{ message?: string }> {
  return deleteRequest(`/documents/${documentId}`);
}

export async function fetchLogs(params: LogsQueryParams): Promise<LogsResponse> {
  return postJSON<LogsResponse>("/logs", params);
}

export async function triggerSharePointSync(): Promise<{ message?: string }> {
  return postJSON("/onedrive/sync", {});
}

export async function getSharePointAuthUrl(): Promise<SharePointAuthUrlResponse> {
  return getJSON<SharePointAuthUrlResponse>("/onedrive/auth-url");
}

export async function fetchConversations(limit = 50): Promise<ApiConversationSummary[]> {
  return getJSON<ApiConversationSummary[]>(`/conversations?limit=${limit}`);
}

export async function fetchConversation(conversationId: string): Promise<ApiConversationDetail> {
  return getJSON<ApiConversationDetail>(`/conversations/${conversationId}`);
}

export async function renameConversationApi(conversationId: string, title: string): Promise<ApiConversationSummary> {
  return patchJSON<ApiConversationSummary>(`/conversations/${conversationId}`, { title });
}

export async function deleteConversationApi(conversationId: string): Promise<{ message?: string }> {
  return deleteRequest(`/conversations/${conversationId}`);
}
