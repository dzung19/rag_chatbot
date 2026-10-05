import React, { useState } from "react";
import { NavLink, useNavigate } from "react-router";
import {
  PanelLeftClose,
  PanelLeftOpen,
  SquarePen,
  FileText,
  Activity,
  Settings,
  EllipsisVertical,
  Pencil,
  Trash2,
  KeyRound,
} from "lucide-react";
import { useUiStore } from "../../stores/uiStore";
import { useChatStore } from "../../stores/chatStore";
import { useHealthStore } from "../../stores/healthStore";
import { useAuthStore } from "../../stores/authStore";
import styles from "./Sidebar.module.css";

export const Sidebar: React.FC = () => {
  const navigate = useNavigate();
  const { sidebarCollapsed, toggleSidebar } = useUiStore();
  const { conversations, order, activeId, newConversation, deleteConversation, renameConversation } =
    useChatStore();
  const { status, modelName } = useHealthStore();
  const { openKeyModal } = useAuthStore();

  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);

  const handleNewChat = () => {
    const id = newConversation();
    navigate(`/c/${id}`);
  };

  const handleSelectConv = (id: string) => {
    navigate(`/c/${id}`);
  };

  const handleRename = (id: string, currentTitle: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setMenuOpenId(null);
    const newTitle = window.prompt("Rename conversation:", currentTitle);
    if (newTitle && newTitle.trim()) {
      renameConversation(id, newTitle.trim());
    }
  };

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setMenuOpenId(null);
    if (window.confirm("Delete this conversation?")) {
      deleteConversation(id);
      if (activeId === id) {
        navigate("/");
      }
    }
  };

  return (
    <aside
      className={`${styles.sidebar} ${sidebarCollapsed ? styles.collapsed : ""}`}
      aria-label="Main Navigation Sidebar"
    >
      {/* Top Section */}
      <div className={styles.topSection}>
        <div className={styles.headerRow}>
          <button
            className={styles.toggleBtn}
            onClick={toggleSidebar}
            title={sidebarCollapsed ? "Expand sidebar (Ctrl+B)" : "Collapse sidebar (Ctrl+B)"}
            aria-label="Toggle sidebar"
          >
            {sidebarCollapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
          </button>
        </div>

        <button
          className={styles.newChatBtn}
          onClick={handleNewChat}
          title="Start new chat"
        >
          <SquarePen size={18} />
          {!sidebarCollapsed && <span>New chat</span>}
        </button>
      </div>

      {/* Recent Chats Section */}
      {!sidebarCollapsed && (
        <div className={styles.recentSection}>
          <div className={styles.recentHeader}>Recent</div>
          {order.length === 0 ? (
            <div style={{ padding: "0.5rem 0.75rem", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
              No conversations yet
            </div>
          ) : (
            order.map((id) => {
              const conv = conversations[id];
              if (!conv) return null;
              const isActive = activeId === id;

              return (
                <div
                  key={id}
                  className={`${styles.convItem} ${isActive ? styles.active : ""}`}
                  onClick={() => handleSelectConv(id)}
                >
                  <span className={styles.convTitle}>{conv.title || "Untitled chat"}</span>

                  <button
                    className={styles.convMenuBtn}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpenId(menuOpenId === id ? null : id);
                    }}
                    title="Conversation options"
                    aria-label="Options"
                  >
                    <EllipsisVertical size={14} />
                  </button>

                  {menuOpenId === id && (
                    <div
                      className={styles.menuDropdown}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        className={styles.menuAction}
                        onClick={(e) => handleRename(id, conv.title, e)}
                      >
                        <Pencil size={13} />
                        <span>Rename</span>
                      </button>
                      <button
                        className={`${styles.menuAction} ${styles.danger}`}
                        onClick={(e) => handleDelete(id, e)}
                      >
                        <Trash2 size={13} />
                        <span>Delete</span>
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Spacer if collapsed */}
      {sidebarCollapsed && <div style={{ flex: 1 }} />}

      {/* Bottom Nav Section */}
      <div className={styles.bottomSection}>
        <NavLink
          to="/documents"
          className={({ isActive }) =>
            `${styles.navItem} ${isActive ? styles.active : ""}`
          }
          title="Documents"
        >
          <FileText size={18} />
          {!sidebarCollapsed && <span>Documents</span>}
        </NavLink>

        <NavLink
          to="/logs"
          className={({ isActive }) =>
            `${styles.navItem} ${isActive ? styles.active : ""}`
          }
          title="Logs"
        >
          <Activity size={18} />
          {!sidebarCollapsed && <span>Logs</span>}
        </NavLink>

        <NavLink
          to="/settings"
          className={({ isActive }) =>
            `${styles.navItem} ${isActive ? styles.active : ""}`
          }
          title="Settings"
        >
          <Settings size={18} />
          {!sidebarCollapsed && <span>Settings</span>}
        </NavLink>

        <button
          className={styles.navItem}
          onClick={openKeyModal}
          title="Change API Key"
          style={{ width: "100%", justifyContent: sidebarCollapsed ? "center" : "flex-start" }}
        >
          <KeyRound size={18} />
          {!sidebarCollapsed && <span>API Key</span>}
        </button>

        {/* Status Indicator */}
        <div
          className={styles.statusRow}
          title={`Status: ${status} | Model: ${modelName}`}
        >
          <span className={`${styles.statusDot} ${styles[status]}`} />
          {!sidebarCollapsed && (
            <span>
              {status === "healthy" ? "Connected" : status === "degraded" ? "Degraded" : "Disconnected"}
            </span>
          )}
        </div>
      </div>
    </aside>
  );
};
