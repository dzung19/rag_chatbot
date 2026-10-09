import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import styles from "./Dialog.module.css";

export interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  icon?: React.ReactNode;
  iconVariant?: "default" | "warning";
  children: React.ReactNode;
  footer?: React.ReactNode;
  maxWidth?: string;
}

export const Dialog: React.FC<DialogProps> = ({
  isOpen,
  onClose,
  title,
  icon,
  iconVariant = "default",
  children,
  footer,
  maxWidth,
}) => {
  const modalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return createPortal(
    <div
      className={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
      role="dialog"
      aria-modal="true"
    >
      <div
        ref={modalRef}
        className={styles.modal}
        style={maxWidth ? { maxWidth } : undefined}
      >
        <div className={styles.header}>
          <div className={styles.headerLeft}>
            {icon && (
              <div
                className={`${styles.iconBox} ${
                  iconVariant === "warning" ? styles.iconBoxWarning : ""
                }`}
              >
                {icon}
              </div>
            )}
            {title && <h3 className={styles.title}>{title}</h3>}
          </div>
          <button
            type="button"
            className={styles.closeButton}
            onClick={onClose}
            aria-label="Đóng hộp thoại"
          >
            <X size={18} />
          </button>
        </div>

        <div className={styles.body}>{children}</div>

        {footer && <div className={styles.footer}>{footer}</div>}
      </div>
    </div>,
    document.body
  );
};
