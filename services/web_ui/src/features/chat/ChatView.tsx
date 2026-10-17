import React, { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useChatStore } from "../../stores/chatStore";
import { Greeting } from "./Greeting";
import { SuggestionCards } from "./SuggestionCards";
import { MessageList } from "./MessageList";
import { Composer } from "./Composer";

export const ChatView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { conversations, selectConversation, resetActive, isStreaming } =
    useChatStore();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      if (useChatStore.getState().activeId !== null) {
        resetActive();
      }
      return;
    }

    let isMounted = true;
    setLoading(true);

    selectConversation(id)
      .catch((err) => {
        console.warn("Failed to load conversation:", err);
        if (isMounted) {
          navigate("/", { replace: true });
        }
      })
      .finally(() => {
        if (isMounted) {
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [id, selectConversation, resetActive, navigate]);

  const currentConv = id ? conversations[id] : null;
  const hasMessages = currentConv && currentConv.messages && currentConv.messages.length > 0;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {Boolean(id) && loading && !currentConv ? (
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-muted)",
            fontSize: "var(--text-sm)",
          }}
        >
          Loading conversation...
        </div>
      ) : !hasMessages ? (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "1rem",
            overflowY: "auto",
          }}
        >
          <Greeting />
          <SuggestionCards />
        </div>
      ) : (
        <MessageList
          conversationId={currentConv.id}
          messages={currentConv.messages}
          isStreaming={isStreaming}
        />
      )}

      <Composer />
    </div>
  );
};
