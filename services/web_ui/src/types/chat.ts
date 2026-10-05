import type { Source } from "./api";

export type MessageRole = "user" | "assistant";

export type MessageStatus = "streaming" | "done" | "error" | "stopped";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  sources: Source[];
  status: MessageStatus;
  feedback?: "up" | "down";
  createdAt: number;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
}

export interface UploadProgressItem {
  id: string;
  filename: string;
  progress: number;
  status: "queued" | "uploading" | "done" | "failed";
  error?: string;
}
