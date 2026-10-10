import { useEffect, useRef, useState } from "react";
import type { DeletedConversation } from "../types";
import styles from "./Admin.module.css";

interface Props {
  conversation: DeletedConversation;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}

export function RestoreDialog({ conversation, submitting, error, onCancel, onConfirm }: Props) {
  const [reason, setReason] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !submitting && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, submitting]);

  const canSubmit = reason.trim().length >= 5 && !submitting;

  return (
    <div className={styles.backdrop} role="presentation" onClick={() => !submitting && onCancel()}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="restore-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="restore-title">Khôi phục cuộc trò chuyện</h3>
        <p className={styles.dialogInfo}>
          <strong>{conversation.title}</strong> sẽ được trả lại cho <strong>{conversation.owner_name}</strong>.
        </p>

        <label htmlFor="restore-reason" className={styles.label}>
          Lý do khôi phục <span className={styles.required}>*</span>
        </label>
        <textarea
          id="restore-reason"
          ref={inputRef}
          className={styles.textarea}
          rows={3}
          placeholder="Ví dụ: Người dùng yêu cầu qua ticket #123"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={submitting}
        />
        <p className={styles.hint}>Tối thiểu 5 ký tự. Lý do được ghi vào nhật ký quản trị.</p>

        {error && <p className={styles.errorText} role="alert">{error}</p>}

        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={onCancel} disabled={submitting}>
            Hủy
          </button>
          <button
            type="button"
            className={styles.primaryButton}
            disabled={!canSubmit}
            onClick={() => onConfirm(reason)}
          >
            {submitting ? "Đang khôi phục..." : "Khôi phục"}
          </button>
        </div>
      </div>
    </div>
  );
}
