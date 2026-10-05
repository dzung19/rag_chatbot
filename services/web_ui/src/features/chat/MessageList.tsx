import React, { useRef, useEffect, useState } from "react";
import { ArrowDown } from "lucide-react";
import type { ChatMessage } from "../../types/chat";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";
import styles from "./MessageList.module.css";

interface MessageListProps {
  conversationId: string;
  messages: ChatMessage[];
  isStreaming: boolean;
}

export const MessageList: React.FC<MessageListProps> = ({
  conversationId,
  messages,
  isStreaming,
}) => {
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  const scrollToBottom = (smooth = true) => {
    bottomAnchorRef.current?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
    });
  };

  // Auto-scroll when messages update or while streaming
  useEffect(() => {
    const el = scrollAreaRef.current;
    if (!el) return;

    // Check if user is near bottom (within 150px)
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 150;
    if (isNearBottom || isStreaming) {
      scrollToBottom(false);
    }
  }, [messages, isStreaming]);

  const handleScroll = () => {
    const el = scrollAreaRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBtn(distanceToBottom > 200);
  };

  return (
    <div
      ref={scrollAreaRef}
      className={styles.scrollArea}
      onScroll={handleScroll}
    >
      <div className={styles.messagesInner}>
        {messages.map((message) =>
          message.role === "user" ? (
            <UserMessage key={message.id} message={message} />
          ) : (
            <AssistantMessage
              key={message.id}
              conversationId={conversationId}
              message={message}
            />
          )
        )}
        <div ref={bottomAnchorRef} />
      </div>

      {showScrollBtn && (
        <button
          className={styles.scrollBottomBtn}
          onClick={() => scrollToBottom(true)}
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
        >
          <ArrowDown size={18} />
        </button>
      )}
    </div>
  );
};
