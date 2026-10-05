import { deleteRequest, getJSON, postJSON, uploadFile } from "./client";
import type {
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
