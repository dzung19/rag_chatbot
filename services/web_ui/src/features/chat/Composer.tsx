import React, { useRef, useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router";
import { Plus, ArrowUp, Square, GitCompare } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
import { useToastStore } from "../../stores/toastStore";
import { useSkillStore } from "../../stores/skillStore";
import { AttachmentChips } from "./AttachmentChips";
import { SkillSelector } from "./SkillSelector";
import { CompareRequirementDialog } from "./CompareRequirementDialog";
import { validateChatFile } from "../../lib/fileValidation";
import { MAX_INPUT_CHARS, MAX_CHAT_FILES } from "../../lib/constants";
import styles from "./Composer.module.css";

export const Composer: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { sendMessage, isStreaming, stopStreaming, activeId, conversations, newConversation } = useChatStore();
  const { pushToast } = useToastStore();
  const { activeMainSkillId } = useSkillStore();

  const [input, setInput] = useState("");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [showCompareDialog, setShowCompareDialog] = useState(false);

  const activeConv = activeId ? conversations[activeId] : null;
  const conversationHas2Files = activeConv?.selectedDocumentIds?.length === 2;
  const hasValidFilesForCompare =
    attachedFiles.length === 2 || (attachedFiles.length === 0 && conversationHas2Files);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  }, [input]);

  const handleAttachClick = () => {
    fileInputRef.current?.click();
  };

  const handleFilesChosen = (files: FileList | null) => {
    if (!files) return;
    const incoming = Array.from(files);
    let updated = [...attachedFiles];

    for (const file of incoming) {
      const res = validateChatFile(file, updated.length, MAX_CHAT_FILES);
      if (!res.valid) {
        pushToast(res.error || "File invalid", "error");
        continue;
      }
      updated.push(file);
    }

    setAttachedFiles(updated);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleRemoveFile = (index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;

    if (activeMainSkillId === "business-compare" && !hasValidFilesForCompare) {
      setShowCompareDialog(true);
      return;
    }

    const filesToSend = [...attachedFiles];
    setInput("");
    setAttachedFiles([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    let targetId = id;
    if (!targetId) {
      targetId = newConversation();
      navigate(`/c/${targetId}`, { replace: true });
    }

    await sendMessage(trimmed, filesToSend);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Drag and drop handlers
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    handleFilesChosen(e.dataTransfer.files);
  };

  const canSend = input.trim().length > 0 && !isStreaming;

  return (
    <div className={styles.composerContainer}>
      <div
        className={`${styles.composerWrapper} ${isDragOver ? styles.dragOver : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <SkillSelector />

        {activeMainSkillId === "business-compare" && (
          <div
            className={`${styles.compareNotice} ${
              !hasValidFilesForCompare ? styles.invalid : ""
            }`}
            onClick={() => setShowCompareDialog(true)}
            role="button"
            tabIndex={0}
            title="Nhấn để xem chi tiết yêu cầu tài liệu so sánh"
          >
            <GitCompare size={14} />
            <span>
              {attachedFiles.length === 2
                ? "So sánh Nghiệp vụ: Đã đính kèm 2 file"
                : conversationHas2Files
                ? "So sánh Nghiệp vụ: Đang đối chiếu 2 file trong hội thoại"
                : `So sánh Nghiệp vụ: Cần chính xác 2 file (${attachedFiles.length}/2)`}
            </span>
            {!hasValidFilesForCompare ? (
              <span className={styles.noticeAlert}>Chưa đủ 2 file</span>
            ) : (
              <span
                className={styles.noticeAlert}
                style={{ color: "var(--badge-green-text)" }}
              >
                ✓ Hợp lệ (Bypass RAG)
              </span>
            )}
          </div>
        )}

        <AttachmentChips files={attachedFiles} onRemove={handleRemoveFile} />

        <div className={styles.inputRow}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.pptx,.xlsx,.txt,.md"
            multiple
            style={{ display: "none" }}
            onChange={(e) => handleFilesChosen(e.target.files)}
          />

          <button
            type="button"
            className={styles.attachBtn}
            onClick={handleAttachClick}
            title="Attach documents (max 2, up to 50MB each)"
            aria-label="Attach documents"
          >
            <Plus size={20} />
          </button>

          <textarea
            ref={textareaRef}
            className={styles.textarea}
            rows={1}
            placeholder="Ask about your documents..."
            value={input}
            maxLength={MAX_INPUT_CHARS}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
          />

          {isStreaming ? (
            <button
              type="button"
              className={`${styles.actionBtn} ${styles.stopBtn}`}
              onClick={stopStreaming}
              title="Stop generating"
              aria-label="Stop generating"
            >
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              className={`${styles.actionBtn} ${styles.sendBtn}`}
              disabled={!canSend}
              onClick={handleSend}
              title="Send question (Enter)"
              aria-label="Send message"
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>

      <div className={styles.footer}>
        {input.length > 4000 && (
          <span className={styles.charCount}>
            {input.length} / {MAX_INPUT_CHARS}
          </span>
        )}
        <span className={styles.disclaimer}>
          Answers are generated from indexed company documents and may contain mistakes.
        </span>
      </div>

      <CompareRequirementDialog
        isOpen={showCompareDialog}
        onClose={() => setShowCompareDialog(false)}
        attachedFiles={attachedFiles}
        onUploadClick={handleAttachClick}
      />
    </div>
  );
};
