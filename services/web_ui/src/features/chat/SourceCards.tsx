import React, { useState } from "react";
import type { Source } from "../../types/api";
import { FileTypeIcon } from "../../components/FileTypeIcon";
import styles from "./SourceCards.module.css";

interface SourceCardsProps {
  sources: Source[];
}

export const SourceCards: React.FC<SourceCardsProps> = ({ sources }) => {
  const [expanded, setExpanded] = useState(false);

  if (!sources || sources.length === 0) return null;

  const visibleSources = expanded ? sources : sources.slice(0, 3);
  const remaining = sources.length - 3;

  return (
    <div className={styles.container}>
      <span className={styles.title}>Sources</span>
      <div className={styles.grid}>
        {visibleSources.map((source, index) => {
          const filename = source.filename || "Document";
          const ext = filename.split(".").pop() || "";
          const scorePercent = (source.score * 100).toFixed(0);

          return (
            <div
              key={index}
              className={styles.card}
              title={`File: ${filename}\nRelevance: ${scorePercent}%`}
            >
              <FileTypeIcon type={ext} size={15} />
              <span className={styles.filename}>{filename}</span>
              <span className={styles.score}>{scorePercent}%</span>
            </div>
          );
        })}

        {remaining > 0 && !expanded && (
          <button
            className={styles.expandBtn}
            onClick={() => setExpanded(true)}
          >
            +{remaining} more
          </button>
        )}
      </div>
    </div>
  );
};
