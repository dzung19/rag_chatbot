import React from "react";
import { Menu, Cpu, Sun, Moon, Sparkles } from "lucide-react";
import { useUiStore } from "../../stores/uiStore";
import { useHealthStore } from "../../stores/healthStore";
import { useChatStore } from "../../stores/chatStore";
import styles from "./TopBar.module.css";

export const TopBar: React.FC = () => {
  const { theme, setTheme, setMobileDrawerOpen } = useUiStore();
  const { modelName } = useHealthStore();
  const { activeId, conversations } = useChatStore();

  const activeConv = activeId ? conversations[activeId] : null;

  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.setAttribute("data-theme", nextTheme);
  };

  return (
    <header className={styles.topBar}>
      <div className={styles.leftSection}>
        <button
          className={styles.mobileMenuBtn}
          onClick={() => setMobileDrawerOpen(true)}
          aria-label="Open navigation menu"
        >
          <Menu size={20} />
        </button>
        <div className={styles.title}>
          <Sparkles size={18} color="var(--accent-purple)" />
          <span>{activeConv?.title || "Knowledge Assistant"}</span>
        </div>
      </div>

      <div className={styles.rightSection}>
        <button
          className={styles.themeBtn}
          onClick={toggleTheme}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </div>
    </header>
  );
};
