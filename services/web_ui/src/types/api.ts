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
  conversation_id?: string;
  request_id?:string;
  skill_id?: string;
  modifier_skill_ids?: string[];
  selected_document_ids?: string[];
}

export interface ApiConversationSummary {
  id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiStoredMessage {
  id: string;
  conversation_id: string;
  turn_id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  status: string;
  sources_json: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiConversationDetail extends ApiConversationSummary {
  messages: ApiStoredMessage[];
}

export interface SharePointAuthUrlResponse {
  url?: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  type: "main" | "modifier";
  category: "base" | "custom";
  is_system: boolean;
  system_prompt_addon: string;
  temperature_override?: number;
  top_k_override?: number;
  icon: string;
  enabled_tools: string[];
  created_at: string;
  updated_at: string;
}

export interface SkillListResponse {
  skills: Skill[];
  total: number;
}
