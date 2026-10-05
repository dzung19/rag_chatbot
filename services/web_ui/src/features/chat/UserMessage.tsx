import React from "react";
import type { ChatMessage } from "../../types/chat";

interface UserMessageProps {
  message: ChatMessage;
}

export const UserMessage: React.FC<UserMessageProps> = ({ message }) => {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        width: "100%",
        padding: "0.5rem 0",
        animation: "slideUp var(--transition-fast) forwards",
      }}
    >
      <div
        style={{
          maxWidth: "80%",
          backgroundColor: "var(--surface-bubble-user)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg) var(--radius-lg) 4px var(--radius-lg)",
          padding: "0.85rem 1.25rem",
          color: "var(--text-primary)",
          fontSize: "var(--text-base)",
          lineHeight: 1.5,
          wordBreak: "break-word",
          whiteSpace: "pre-wrap",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        {message.content}
      </div>
    </div>
  );
};
