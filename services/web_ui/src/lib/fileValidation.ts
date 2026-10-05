import { ALLOWED_EXTENSIONS, MAX_CHAT_FILES, MAX_FILE_SIZE_BYTES } from "./constants";
import { formatBytes } from "./formatBytes";

export interface FileValidationResult {
  valid: boolean;
  error?: string;
}

export function validateChatFile(
  file: File,
  currentCount: number,
  maxFiles: number = MAX_CHAT_FILES
): FileValidationResult {
  if (currentCount >= maxFiles) {
    return {
      valid: false,
      error: `Maximum ${maxFiles} files allowed.`,
    };
  }

  const ext = ("." + (file.name.split(".").pop() || "")).toLowerCase();
  const allowed = (ALLOWED_EXTENSIONS as readonly string[]).includes(ext);
  if (!allowed) {
    return {
      valid: false,
      error: `File type "${ext}" not supported. Allowed: ${ALLOWED_EXTENSIONS.join(", ")}`,
    };
  }

  if (file.size > MAX_FILE_SIZE_BYTES) {
    return {
      valid: false,
      error: `File "${file.name}" is too large (${formatBytes(file.size)}). Max allowed is ${formatBytes(MAX_FILE_SIZE_BYTES)}.`,
    };
  }

  return { valid: true };
}
