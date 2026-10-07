import React, { useState } from "react";
import {
  Palette,
  KeyRound,
  Trash2,
  Sun,
  Moon,
  Monitor,
  LogOut,
  UserRound,
} from "lucide-react";
import { useUiStore } from "../../stores/uiStore";
import { useChatStore } from "../../stores/chatStore";
import { useToastStore } from "../../stores/toastStore";
import styles from "./SettingsView.module.css";
import { useAuthStore } from "../../stores/authStore";

export const SettingsView: React.FC = () => {
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const { theme, setTheme, userName, setUserName } = useUiStore();
  const { clearAll, conversations } = useChatStore();
  const { pushToast } = useToastStore();

  const [nameInput, setNameInput] = useState(userName);

  const handleNameSave = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = nameInput.trim();
    if (trimmed) {
      setUserName(trimmed);
      pushToast("Display name updated", "success");
    }
  };

  const handleClearHistory = () => {
    if (
      window.confirm(
        "Are you sure you want to delete all chat history? This cannot be undone."
      )
    ) {
      clearAll();
      pushToast("All chat history cleared", "info");
    }
  };

  return (
    <div className={styles.container}>
      <div className={styles.headerRow}>
        <h1>Settings</h1>
        <p>Manage application preferences, authentication, and conversation storage</p>
      </div>

      {/* Appearance Section */}
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          <Palette size={18} />
          <span>Appearance & Profile</span>
        </div>

        <div className={styles.row}>
          <div className={styles.labelGroup}>
            <span className={styles.label}>Interface Theme</span>
            <span className={styles.description}>Select color mode for the assistant</span>
          </div>

          <div className={styles.themeSelector}>
            <button
              className={`${styles.themeBtn} ${theme === "dark" ? styles.active : ""}`}
              onClick={() => {
                setTheme("dark");
                document.documentElement.setAttribute("data-theme", "dark");
              }}
            >
              <Moon size={14} />
              <span>Dark</span>
            </button>

            <button
              className={`${styles.themeBtn} ${theme === "light" ? styles.active : ""}`}
              onClick={() => {
                setTheme("light");
                document.documentElement.setAttribute("data-theme", "light");
              }}
            >
              <Sun size={14} />
              <span>Light</span>
            </button>

            <button
              className={`${styles.themeBtn} ${theme === "system" ? styles.active : ""}`}
              onClick={() => {
                setTheme("system");
                const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                document.documentElement.setAttribute(
                  "data-theme",
                  prefersDark ? "dark" : "light"
                );
              }}
            >
              <Monitor size={14} />
              <span>System</span>
            </button>
          </div>
        </div>

        <form onSubmit={handleNameSave} className={styles.row}>
          <div className={styles.labelGroup}>
            <span className={styles.label}>Your Name</span>
            <span className={styles.description}>Used for personal greeting in chat</span>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="text"
              className={styles.input}
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              maxLength={40}
            />
            <button type="submit" className={`${styles.btn} ${styles.primaryBtn}`}>
              Save
            </button>
          </div>
        </form>
      </div>

      {/* Authentication Section */}

      {/* Data & Privacy Section */}
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          <Trash2 size={18} />
          <span>Storage & Chat History</span>
        </div>

        <div className={styles.row}>
          <div className={styles.labelGroup}>
            <span className={styles.label}>Conversations</span>
            <span className={styles.description}>
              {Object.keys(conversations).length} conversation(s) stored locally in browser
            </span>
          </div>

          <button
            className={`${styles.btn} ${styles.dangerBtn}`}
            onClick={handleClearHistory}
            disabled={Object.keys(conversations).length === 0}
          >
            Clear All History
          </button>
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          <KeyRound size={18} />
          <span>Authentication</span>
        </div>


        <div className={styles.row}>

          <div className={styles.userGroup}>
            <UserRound size={20} aria-hidden="true" />
            <div className={styles.userText}>
              <strong>{user ? user.name : ""}</strong>
              <span>{user?.email}</span>
            </div>
          </div>

          <button
            type="button"
            className={styles.logoutButton}
            aria-label="Sign out"
            title="Sign out"
            onClick={logout}
          >
            <LogOut size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
};
