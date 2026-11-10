import React, { useState } from "react";
import { Copy, Check, RefreshCw, ThumbsUp, ThumbsDown, FileSpreadsheet, Loader2 } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
import { useToastStore } from "../../stores/toastStore";
import { exportCompareExcelApi } from "../../api/endpoints";
import styles from "./MessageActions.module.css";

interface MessageActionsProps {
  conversationId: string;
  messageId: string;
  content: string;
  feedback?: "up" | "down";
}

export const MessageActions: React.FC<MessageActionsProps> = ({
  conversationId,
  messageId,
  content,
  feedback,
}) => {
  const { regenerate, setFeedback, isStreaming } = useChatStore();
  const { pushToast } = useToastStore();
  const [copied, setCopied] = useState(false);
  const [isExporting, setIsExporting] = useState(false);

  // Check if content contains comparison tables (Table 1: TỔNG HỢP ĐIỂM ĐÚNG or Table 2: ĐIỂM CẦN XỬ LÝ)
  const isCompareReport =
    /##\s*1\.\s*TỔNG HỢP ĐIỂM ĐÚNG/i.test(content) ||
    /##\s*2\.\s*ĐIỂM CẦN XỬ LÝ/i.test(content) ||
    /Tổng số điểm đúng:\s*\d+/i.test(content);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Ignore
    }
  };

  const handleRegenerate = () => {
    if (!isStreaming) {
      regenerate(messageId);
    }
  };

  const handleExportExcel = async () => {
    if (isExporting) return;
    setIsExporting(true);
    try {
      const blob = await exportCompareExcelApi(content, "Bao_cao_doi_chieu_BL");
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const nowStr = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      a.download = `Ket_qua_doi_chieu_BL_${nowStr}.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      pushToast("Xuất file Excel thành công! (2 sheet: Điểm đúng, Điểm sai & Không tìm thấy)", "success");
    } catch (err) {
      console.error("Export Excel error:", err);
      pushToast("Xuất file Excel thất bại. Vui lòng thử lại sau.", "error");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className={styles.actionBar}>
      {isCompareReport && (
        <button
          className={styles.excelBtn}
          onClick={handleExportExcel}
          disabled={isExporting}
          title="Xuất kết quả đối chiếu ra file Excel (2 sheet)"
          aria-label="Xuất kết quả đối chiếu ra file Excel"
        >
          {isExporting ? <Loader2 size={13} className="spin" /> : <FileSpreadsheet size={13} />}
          <span>{isExporting ? "Đang xuất..." : "Xuất Excel"}</span>
        </button>
      )}

      <button
        className={styles.actionBtn}
        onClick={handleCopy}
        title="Copy response"
        aria-label="Copy response"
      >
        {copied ? (
          <Check size={14} color="var(--badge-green-text)" />
        ) : (
          <Copy size={14} />
        )}
      </button>

      <button
        className={styles.actionBtn}
        onClick={handleRegenerate}
        disabled={isStreaming}
        title="Regenerate response"
        aria-label="Regenerate response"
      >
        <RefreshCw size={14} />
      </button>

      <button
        className={`${styles.actionBtn} ${feedback === "up" ? styles.active : ""}`}
        onClick={() => setFeedback(conversationId, messageId, "up")}
        title="Good response"
        aria-label="Thumbs up"
      >
        <ThumbsUp size={14} />
      </button>

      <button
        className={`${styles.actionBtn} ${feedback === "down" ? styles.active : ""}`}
        onClick={() => setFeedback(conversationId, messageId, "down")}
        title="Bad response"
        aria-label="Thumbs down"
      >
        <ThumbsDown size={14} />
      </button>
    </div>
  );
};
