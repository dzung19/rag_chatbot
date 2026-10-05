import React from "react";
import { X } from "lucide-react";
import { useUiStore } from "../../stores/uiStore";
import { Sidebar } from "./Sidebar";

export const MobileDrawer: React.FC = () => {
  const { mobileDrawerOpen, setMobileDrawerOpen } = useUiStore();

  if (!mobileDrawerOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.6)",
        backdropFilter: "blur(4px)",
        zIndex: 9000,
        display: "flex",
      }}
      onClick={() => setMobileDrawerOpen(false)}
    >
      <div
        style={{
          width: "280px",
          height: "100%",
          backgroundColor: "var(--surface-sidebar)",
          display: "flex",
          flexDirection: "column",
          position: "relative",
          animation: "slideDown 0.2s ease-out",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            padding: "0.75rem",
          }}
        >
          <button
            onClick={() => setMobileDrawerOpen(false)}
            style={{
              padding: "4px",
              color: "var(--text-secondary)",
              borderRadius: "var(--radius-sm)",
            }}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <Sidebar />
        </div>
      </div>
    </div>
  );
};
