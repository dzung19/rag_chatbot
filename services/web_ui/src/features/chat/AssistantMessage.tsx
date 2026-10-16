import React from "react";
import { Sparkles } from "lucide-react";
import type { ChatMessage } from "../../types/chat";
import { MarkdownContent } from "./MarkdownContent";
import { SourceCards } from "./SourceCards";
import { MessageActions } from "./MessageActions";
import { ThinkingShimmer } from "./ThinkingShimmer";

interface AssistantMessageProps {
  conversationId: string;
  message: ChatMessage;
  isStreaming?: boolean;
}

export const AssistantMessage: React.FC<AssistantMessageProps> = ({
  conversationId,
  message,
  isStreaming = false,
}) => {
  const isActivelyStreaming = isStreaming && message.status === "streaming";
  const isThinking = isActivelyStreaming && !message.content;

  return (
    <div
      style={{
        display: "flex",
        gap: "1rem",
        width: "100%",
        padding: "0.75rem 0 1.25rem",
        animation: "slideUp var(--transition-fast) forwards",
      }}
    >
      {/* Sparkles avatar */}
      <div
        style={{
          width: "32px",
          height: "32px",
          borderRadius: "50%",
          background: "var(--surface-hover)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          marginTop: "2px",
        }}
      >
        <Sparkles size={18} color="var(--accent-purple)" />
      </div>

      {/* Message Content Container */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {isThinking ? (
          <ThinkingShimmer />
        ) : (
          <>
            {message.content ? (
              <MarkdownContent content={message.content} />
            ) : (
              !isActivelyStreaming &&
              message.status !== "error" && (
                <div
                  style={{
                    fontSize: "var(--text-xs)",
                    color: "var(--text-muted)",
                    fontStyle: "italic",
                  }}
                >
                  (Không có phản hồi được lưu cho lượt hội thoại này)
                </div>
              )
            )}

            {/* Blinking cursor while streaming */}
            {isActivelyStreaming && (
              <span
                style={{
                  display: "inline-block",
                  width: "8px",
                  height: "16px",
                  backgroundColor: "var(--accent-purple)",
                  marginLeft: "4px",
                  verticalAlign: "middle",
                  animation: "pulseGlow 0.8s infinite",
                }}
              />
            )}

            {/* Stopped notice */}
            {!isActivelyStreaming && message.status === "stopped" && message.content && (
              <div
                style={{
                  fontSize: "var(--text-xs)",
                  color: "var(--text-muted)",
                  marginTop: "0.5rem",
                  fontStyle: "italic",
                }}
              >
                (Generation stopped)
              </div>
            )}

            {/* Error notice */}
            {message.status === "error" && (
              <div
                style={{
                  fontSize: "var(--text-xs)",
                  color: "var(--badge-red-text)",
                  marginTop: "0.5rem",
                }}
              >
                An error occurred during response generation.
              </div>
            )}

            {/* Source citations */}
            <SourceCards sources={message.sources} />

            {/* Message Action icons (copy, regenerate, vote) */}
            {!isActivelyStreaming && (
              <MessageActions
                conversationId={conversationId}
                messageId={message.id}
                content={message.content}
                feedback={message.feedback}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
};
