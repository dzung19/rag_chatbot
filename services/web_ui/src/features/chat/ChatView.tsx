import React, { useEffect } from "react";
import { useParams, useNavigate } from "react-router";
import { useChatStore } from "../../stores/chatStore";
import { Greeting } from "./Greeting";
import { SuggestionCards } from "./SuggestionCards";
import { MessageList } from "./MessageList";
import { Composer } from "./Composer";

export const ChatView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { conversations, activeId, selectConversation, isStreaming } =
    useChatStore();

  useEffect(() => {
    if (id) {
      if (conversations[id]) {
        selectConversation(id);
      } else {
        // Unknown id -> navigate to root
        navigate("/", { replace: true });
      }
    }
  }, [id, conversations, selectConversation, navigate]);

  const currentConv = (id && conversations[id]) || (activeId && conversations[activeId]);
  const hasMessages = currentConv && currentConv.messages.length > 0;

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
      {!hasMessages ? (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            paddingBottom: "130px",
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
