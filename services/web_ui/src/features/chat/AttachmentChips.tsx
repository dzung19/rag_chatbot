import React from "react";
import { X } from "lucide-react";
import { FileTypeIcon } from "../../components/FileTypeIcon";

interface AttachmentChipsProps {
  files: File[];
  onRemove: (index: number) => void;
}

export const AttachmentChips: React.FC<AttachmentChipsProps> = ({ files, onRemove }) => {
  if (files.length === 0) return null;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "0.5rem",
        marginBottom: "0.5rem",
        padding: "0 0.5rem",
      }}
    >
      {files.map((file, index) => {
        const ext = file.name.split(".").pop() || "";
        return (
          <div
            key={index}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.3rem 0.65rem",
              backgroundColor: "var(--surface-hover)",
              border: "1px solid var(--border-default)",
              borderRadius: "var(--radius-pill)",
              fontSize: "var(--text-xs)",
              color: "var(--text-primary)",
              maxWidth: "240px",
            }}
          >
            <FileTypeIcon type={ext} size={14} />
            <span
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {file.name}
            </span>
            <button
              onClick={() => onRemove(index)}
              style={{
                display: "flex",
                alignItems: "center",
                color: "var(--text-muted)",
                padding: "2px",
                borderRadius: "50%",
              }}
              aria-label={`Remove ${file.name}`}
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
};
