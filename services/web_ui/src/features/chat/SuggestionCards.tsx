import React from "react";
import { ListChecks, NotebookPen, Search, Lightbulb } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
import styles from "./SuggestionCards.module.css";

interface SuggestionCardsProps {
  onSelectPrompt?: (text: string) => void;
}

export const SuggestionCards: React.FC<SuggestionCardsProps> = ({ onSelectPrompt }) => {
  const { sendMessage } = useChatStore();

  const suggestions = [
    {
      text: "What documents are available?",
      icon: <ListChecks size={18} />,
    },
    {
      text: "Summarize key points from documents",
      icon: <NotebookPen size={18} />,
    },
    {
      text: "What are the main topics covered?",
      icon: <Search size={18} />,
    },
    {
      text: "Find policy on remote work",
      icon: <Lightbulb size={18} />,
    },
  ];

  const handleClick = (text: string) => {
    if (onSelectPrompt) {
      onSelectPrompt(text);
    } else {
      sendMessage(text);
    }
  };

  return (
    <div className={styles.grid}>
      {suggestions.map((item, index) => (
        <button
          key={index}
          className={styles.card}
          onClick={() => handleClick(item.text)}
        >
          <span className={styles.text}>{item.text}</span>
          <div className={styles.iconWrapper}>{item.icon}</div>
        </button>
      ))}
    </div>
  );
};
