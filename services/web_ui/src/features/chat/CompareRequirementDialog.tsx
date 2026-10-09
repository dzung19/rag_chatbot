import React from "react";
import { GitCompare, FileText, AlertTriangle, Plus } from "lucide-react";
import { Dialog } from "../../components/ui/Dialog";
import styles from "./CompareRequirementDialog.module.css";

interface CompareRequirementDialogProps {
  isOpen: boolean;
  onClose: () => void;
  attachedFiles: File[];
  onUploadClick: () => void;
}

export const CompareRequirementDialog: React.FC<CompareRequirementDialogProps> = ({
  isOpen,
  onClose,
  attachedFiles,
  onUploadClick,
}) => {
  const count = attachedFiles.length;

  const handleUploadClick = () => {
    onClose();
    // Allow state to settle before triggering file picker
    setTimeout(() => {
      onUploadClick();
    }, 150);
  };

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title="Chỉ hỗ trợ so sánh 2 file"
      icon={<GitCompare size={20} />}
      iconVariant="warning"
      footer={
        <>
          {count < 2 ? (
            <>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={onClose}
              >
                Đóng
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={handleUploadClick}
              >
                <Plus size={16} />
                {count === 0 ? "Chọn 2 tài liệu" : "Đính kèm thêm 1 file"}
              </button>
            </>
          ) : (
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={onClose}
            >
              Đã hiểu
            </button>
          )}
        </>
      }
    >
      <div className={styles.content}>
        <p className={styles.description}>
          Tính năng <strong>So sánh Nghiệp vụ</strong> được tối ưu hóa để đối
          chiếu toàn văn giữa <strong>chính xác 2 tài liệu</strong>. Khi kích
          hoạt, hệ thống sẽ tự động bỏ qua tìm kiếm phân đoạn (RAG thông thường)
          để nạp trực tiếp toàn bộ dữ liệu của 2 file vào ngữ cảnh phân tích.
        </p>

        <div className={styles.statusCard}>
          <div className={styles.statusHeader}>
            <span>Trạng thái tập tin</span>
            <span className={styles.badgeWarning}>
              <AlertTriangle size={12} />
              {count} / 2 tài liệu
            </span>
          </div>

          <div className={styles.fileList}>
            {count === 0 ? (
              <div className={styles.emptyState}>
                Chưa có tài liệu nào được đính kèm vào khung chat.
              </div>
            ) : (
              attachedFiles.map((file, idx) => (
                <div key={`${file.name}-${idx}`} className={styles.fileItem}>
                  <FileText size={14} />
                  <span>
                    <strong>File {idx + 1}:</strong> {file.name}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        <p className={styles.hint}>
          💡 Vui lòng đính kèm đúng 2 tài liệu (PDF, DOCX, XLSX, PPTX, TXT, MD)
          để hệ thống có thể tiến hành phân tích và so sánh.
        </p>
      </div>
    </Dialog>
  );
};
