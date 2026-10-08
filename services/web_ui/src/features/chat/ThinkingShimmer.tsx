import React from "react";
import styles from "./ThinkingShimmer.module.css";

/** Three bouncing dots shown while waiting for the first token (ChatGPT/Copilot style). */
export const ThinkingShimmer: React.FC = () => {
  return (
    <div className={styles.typing} role="status" aria-label="Assistant is thinking">
      <span className={styles.dot} />
      <span className={styles.dot} />
      <span className={styles.dot} />
    </div>
  );
};
