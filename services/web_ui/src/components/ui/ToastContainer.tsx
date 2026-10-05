import React from "react";
import { CircleCheck, CircleAlert, Info, X } from "lucide-react";
import { useToastStore, type ToastItem } from "../../stores/toastStore";
import styles from "./Toast.module.css";

export const ToastContainer: React.FC = () => {
  const { toasts, dismissToast } = useToastStore();

  if (toasts.length === 0) return null;

  return (
    <div className={styles.container}>
      {toasts.map((toast: ToastItem) => (
        <div key={toast.id} className={`${styles.toast} ${styles[toast.type]}`}>
          {toast.type === "success" && <CircleCheck size={18} color="var(--badge-green-text)" />}
          {toast.type === "error" && <CircleAlert size={18} color="var(--badge-red-text)" />}
          {toast.type === "info" && <Info size={18} color="var(--accent-blue)" />}
          <span className={styles.message}>{toast.message}</span>
          <button
            className={styles.closeBtn}
            onClick={() => dismissToast(toast.id)}
            aria-label="Dismiss toast"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
};
