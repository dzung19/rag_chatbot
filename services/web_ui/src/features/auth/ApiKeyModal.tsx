import React, { useState } from "react";
import { KeyRound } from "lucide-react";
import { useAuthStore } from "../../stores/authStore";
import { useHealthStore } from "../../stores/healthStore";
import { useToastStore } from "../../stores/toastStore";
import styles from "./ApiKeyModal.module.css";

export const ApiKeyModal: React.FC = () => {
  const { isKeyModalOpen, apiKey, setApiKey, closeKeyModal } = useAuthStore();
  const { checkHealth } = useHealthStore();
  const { pushToast } = useToastStore();
  const [inputValue, setInputValue] = useState("");

  if (!isKeyModalOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = inputValue.trim();
    if (!key) return;

    setApiKey(key);
    pushToast("API Key updated. Connecting...", "info");
    await checkHealth();
    closeKeyModal();
  };

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.iconWrapper}>
            <KeyRound size={22} />
          </div>
          <h2 id="modal-title" className={styles.title}>
            API Key Required
          </h2>
        </div>
        <p className={styles.description}>
          Enter your company API key to authenticate with the API Gateway and start querying documents.
        </p>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.inputGroup}>
            <label htmlFor="api-key-input" className={styles.label}>
              Secret Key
            </label>
            <input
              id="api-key-input"
              type="password"
              className={styles.input}
              placeholder="Enter your API key"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              autoFocus
              maxLength={256}
            />
          </div>

          <div className={styles.actions}>
            {apiKey && (
              <button
                type="button"
                className={styles.cancelBtn}
                onClick={closeKeyModal}
                style={{
                  padding: "0.75rem 1.25rem",
                  color: "var(--text-secondary)",
                  fontSize: "var(--text-sm)",
                  borderRadius: "var(--radius-pill)",
                }}
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              className={styles.submitBtn}
              disabled={!inputValue.trim()}
            >
              Connect
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
