import React from "react";
import { Sparkles } from "lucide-react";
import styles from "./ThinkingShimmer.module.css";

export const ThinkingShimmer: React.FC = () => {
  return (
    <div className={styles.shimmer}>
      <Sparkles size={16} className={styles.sparkle} />
      <span>Searching documents and reasoning...</span>
    </div>
  );
};
