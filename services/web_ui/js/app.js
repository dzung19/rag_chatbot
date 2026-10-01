/**
 * RAG Chatbot — Main Application Logic
 *
 * Handles: Core App initialization, API Key Management, Navigation, Health checks.
 * Security: All DOM manipulation via createElement/textContent (no innerHTML).
 */

document.addEventListener("DOMContentLoaded", () => {
    "use strict";

    // -----------------------------------------------------------------------
    // DOM References
    // -----------------------------------------------------------------------
    const sidebar = document.getElementById("sidebar");
    const navBtns = document.querySelectorAll(".nav-btn");
    const views = document.querySelectorAll(".view");

    // API Key Modal
    const apiKeyModal = document.getElementById("api-key-modal");
    const apiKeyInput = document.getElementById("api-key-input");
    const apiKeySubmit = document.getElementById("api-key-submit");

    // Connection Status
    const connectionStatus = document.getElementById("connection-status");
    const statusDot = connectionStatus.querySelector(".status-dot");
    const statusText = connectionStatus.querySelector(".status-text");

    // -----------------------------------------------------------------------
    // Initialize Submodules
    // -----------------------------------------------------------------------
    if (typeof window.initChat === "function") window.initChat();
    if (typeof window.initDocuments === "function") window.initDocuments();
    if (typeof window.initLogs === "function") window.initLogs();

    // -----------------------------------------------------------------------
    // API Key Management
    // -----------------------------------------------------------------------
    const storedKey = sessionStorage.getItem("rag_api_key");
    if (storedKey) {
        ApiClient.setApiKey(storedKey);
        apiKeyModal.classList.add("hidden");
        checkHealth();
    }

    apiKeySubmit.addEventListener("click", () => {
        const key = apiKeyInput.value.trim();
        if (key) {
            ApiClient.setApiKey(key);
            // Store in sessionStorage (cleared on tab close) — not localStorage
            sessionStorage.setItem("rag_api_key", key);
            apiKeyModal.classList.add("hidden");
            checkHealth();
        }
    });

    apiKeyInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            apiKeySubmit.click();
        }
    });

    // -----------------------------------------------------------------------
    // Navigation
    // -----------------------------------------------------------------------
    navBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            const viewName = btn.dataset.view;
            navBtns.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            views.forEach((v) => v.classList.remove("active"));
            const targetView = document.getElementById(`view-${viewName}`);
            if (targetView) {
                targetView.classList.add("active");
            }

            // Auto-refresh documents when switching to documents view
            if (viewName === "documents" && typeof window.loadDocuments === "function") {
                window.loadDocuments();
            }
        });
    });

    // -----------------------------------------------------------------------
    // Health Check
    // -----------------------------------------------------------------------
    async function checkHealth() {
        try {
            const data = await ApiClient.healthCheck();
            statusDot.className = "status-dot connected";
            statusText.textContent = data.status === "healthy" ? "Connected" : "Degraded";
            
            // Dynamically update active model name in the UI
            if (data.model_name) {
                const modelInfo = document.getElementById("model-info");
                if (modelInfo) {
                    modelInfo.textContent = data.model_name;
                }
            }
        } catch {
            statusDot.className = "status-dot error";
            statusText.textContent = "Disconnected";
        }
    }

    // Periodic health check
    setInterval(checkHealth, 30000);
});
