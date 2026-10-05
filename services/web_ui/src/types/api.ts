export type DocumentStatus = "indexed" | "failed" | "processing" | "pending";

export interface DocumentInfo {
  document_id: string;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  chunk_count: number;
  status: DocumentStatus;
  uploaded_at?: string;
  error_message?: string;
}

export interface DocumentsResponse {
  documents: DocumentInfo[];
  total?: number;
}

export interface Source {
  document_id?: string;
  filename?: string;
  chunk_index?: number;
  content?: string;
  score: number;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  model_name?: string;
  version?: string;
  timestamp?: string;
}

export interface LogEntry {
  timestamp: string;
  service: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  message: string;
  request_id?: string;
}

export interface LogsQueryParams {
  service?: string | null;
  level?: string | null;
  search?: string | null;
  limit?: number;
}

export interface LogsResponse {
  logs: LogEntry[];
}

export interface ChatQueryParams {
  query: string;
  top_k?: number;
  temperature?: number;
  stream?: boolean;
}

export interface SharePointAuthUrlResponse {
  url?: string;
}
