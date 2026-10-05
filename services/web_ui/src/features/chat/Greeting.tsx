import React from "react";
import { useUiStore } from "../../stores/uiStore";
import styles from "./Greeting.module.css";

export const Greeting: React.FC = () => {
  const { userName } = useUiStore();

  return (
    <div className={styles.container}>
      <h1 className={styles.gradientTitle}>
        Hello, {userName || "there"}
      </h1>
      <p className={styles.subtitle}>
        How can I help with your company documents today?
      </p>
    </div>
  );
};
