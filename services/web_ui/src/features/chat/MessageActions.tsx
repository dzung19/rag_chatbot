import React, { useState } from "react";
import { Copy, Check, RefreshCw, ThumbsUp, ThumbsDown } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
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
  const [copied, setCopied] = useState(false);

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

  return (
    <div className={styles.actionBar}>
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
