/**
 * RAG Chatbot — Main Application Logic
 *
 * Handles: Chat (streaming + sources), Document Management, Log Viewer.
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

    // Chat
    const chatMessages = document.getElementById("chat-messages");
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const compareLeft = document.getElementById("compare-left");
    const compareRight = document.getElementById("compare-right");
    const charCount = document.getElementById("char-count");
    const welcomeMessage = document.getElementById("welcome-message");

    // Documents
    const uploadZone = document.getElementById("upload-zone");
    const fileInput = document.getElementById("file-input");
    const uploadBtn = document.getElementById("upload-btn");
    const uploadProgressList = document.getElementById("upload-progress-list");
    const documentList = document.getElementById("document-list");
    const refreshDocsBtn = document.getElementById("refresh-docs-btn");
    const syncSharepointBtn = document.getElementById("sync-sharepoint-btn");

    // Logs
    const logServiceFilter = document.getElementById("log-service-filter");
    const logLevelFilter = document.getElementById("log-level-filter");
    const logSearch = document.getElementById("log-search");
    const logQueryBtn = document.getElementById("log-query-btn");
    const logTableBody = document.getElementById("log-table-body");

    // API Key Modal
    const apiKeyModal = document.getElementById("api-key-modal");
    const apiKeyInput = document.getElementById("api-key-input");
    const apiKeySubmit = document.getElementById("api-key-submit");

    // Connection Status
    const connectionStatus = document.getElementById("connection-status");
    const statusDot = connectionStatus.querySelector(".status-dot");
    const statusText = connectionStatus.querySelector(".status-text");

    // Hint buttons
    const hintBtns = document.querySelectorAll(".hint-btn");

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    let isStreaming = false;

    // -----------------------------------------------------------------------
    // API Key Management
    // -----------------------------------------------------------------------
    const storedKey = sessionStorage.getItem("rag_api_key");
    if (storedKey) {
        ApiClient.setApiKey(storedKey);
        apiKeyModal.classList.add("hidden");
        checkHealth();
        loadDocuments();
    }

    apiKeySubmit.addEventListener("click", () => {
        const key = apiKeyInput.value.trim();
        if (key) {
            ApiClient.setApiKey(key);
            // Store in sessionStorage (cleared on tab close) — not localStorage
            sessionStorage.setItem("rag_api_key", key);
            apiKeyModal.classList.add("hidden");
            checkHealth();
        loadDocuments();
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
            if (viewName === "documents" || viewName === "chat") {
                loadDocuments();
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

    // -----------------------------------------------------------------------
    // Chat
    // -----------------------------------------------------------------------

    // Auto-resize textarea
    chatInput.addEventListener("input", () => {
        chatInput.style.height = "auto";
        chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + "px";
        charCount.textContent = `${chatInput.value.length} / 5000`;
        sendBtn.disabled = chatInput.value.trim().length === 0 || isStreaming;
    });

    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!sendBtn.disabled) {
                sendMessage();
            }
        }
    });

    sendBtn.addEventListener("click", sendMessage);

    // Hint buttons
    hintBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            chatInput.value = btn.dataset.hint;
            chatInput.dispatchEvent(new Event("input"));
            sendMessage();
        });
    });

    function sendMessage() {
        const query = chatInput.value.trim();
        if (!query || isStreaming) return;

        const leftId = compareLeft.value, rightId = compareRight.value;
        if ((leftId && !rightId) || (!leftId && rightId) || (leftId && leftId === rightId)) {
            appendMessage("assistant", "Select two different PDFs or clear both selections."); return;
        }
        const selectedDocumentIds = leftId && rightId ? [leftId, rightId] : [];
        // Hide welcome message
        if (welcomeMessage) {
            welcomeMessage.style.display = "none";
        }

        // Add user message
        appendMessage("user", query);

        // Clear input
        chatInput.value = "";
        chatInput.style.height = "auto";
        charCount.textContent = "0 / 5000";
        sendBtn.disabled = true;
        isStreaming = true;

        // Add typing indicator
        const typingEl = createTypingIndicator();
        chatMessages.appendChild(typingEl);
        scrollToBottom();

        // Start streaming response
        let responseText = "";
        let responseSources = [];
        let comparisonResult = null;
        let messageEl = null;

        ApiClient.streamChatQuery(
            { query, top_k: 5, temperature: 0.7, stream: true, selected_document_ids: selectedDocumentIds },
            {
                onSources: (sources) => {
                    responseSources = sources;
                },
                onComparison: (data) => { comparisonResult = data; },
                onToken: (token) => {
                    // Remove typing indicator on first token
                    if (typingEl.parentNode) {
                        typingEl.remove();
                    }

                    responseText += token;

                    if (!messageEl) {
                        messageEl = appendMessage("assistant", responseText);
                    } else {
                        // Update existing message content
                        const contentEl = messageEl.querySelector(".message-text");
                        if (contentEl) {
                            renderFormattedText(contentEl, responseText);
                        }
                    }
                    scrollToBottom();
                },
                onDone: () => {
                    if (typingEl.parentNode) {
                        typingEl.remove();
                    }
                    isStreaming = false;
                    sendBtn.disabled = chatInput.value.trim().length === 0;

                    // Add sources if available
                    if (messageEl && responseSources.length > 0) {
                        const contentWrapper = messageEl.querySelector(".message-content");
                        if (contentWrapper) {
                            appendSources(contentWrapper, responseSources);
                        }
                    }

                    // If no response received, show fallback
                    if (!messageEl) {
                        messageEl = appendMessage("assistant", responseText || "No response received. Please try again.");
                    }
                    if (comparisonResult) appendComparison(messageEl.querySelector(".message-content"), comparisonResult);
                    scrollToBottom();
                },
                onError: (error) => {
                    if (typingEl.parentNode) {
                        typingEl.remove();
                    }
                    isStreaming = false;
                    sendBtn.disabled = chatInput.value.trim().length === 0;
                    
                    if (error.includes("401") || error.includes("403") || error.toLowerCase().includes("forbidden") || error.toLowerCase().includes("api key")) {
                        appendMessage("assistant", "Authentication failed. The API key is invalid or expired. Please check your key.");
                        sessionStorage.removeItem("rag_api_key");
                        apiKeyModal.classList.remove("hidden");
                    } else {
                        appendMessage("assistant", "Sorry, an error occurred. Please try again.");
                    }
                    scrollToBottom();
                },
            }
        );
    }

    /**
     * Append a chat message to the messages container.
     * Uses createElement/textContent exclusively — no innerHTML.
     */
    function appendMessage(role, text) {
        const messageDiv = document.createElement("div");
        messageDiv.className = `message ${role}`;

        const avatarDiv = document.createElement("div");
        avatarDiv.className = "message-avatar";
        avatarDiv.textContent = role === "user" ? "👤" : "🤖";

        const contentDiv = document.createElement("div");
        contentDiv.className = "message-content";

        const textDiv = document.createElement("div");
        textDiv.className = "message-text";
        renderFormattedText(textDiv, text);

        contentDiv.appendChild(textDiv);
        messageDiv.appendChild(avatarDiv);
        messageDiv.appendChild(contentDiv);
        chatMessages.appendChild(messageDiv);

        return messageDiv;
    }

    /**
     * Parse markdown-like syntax (paragraphs, lists, bold) and render safely.
     * Guaranteed XSS-safe since it uses createElement and textContent exclusively.
     */
    function renderFormattedText(container, text) {
        container.replaceChildren();

        // Clean LaTeX math arrows to simple Unicode arrows
        const cleanedText = text
            .replace(/\$\\(?:right|left|up|down)arrow\$/gi, (m) => m.toLowerCase().includes("right") ? "→" : m.toLowerCase().includes("left") ? "←" : m.toLowerCase().includes("up") ? "↑" : "↓")
            .replace(/\\(?:right|left|up|down)arrow/gi, (m) => m.toLowerCase().includes("right") ? "→" : m.toLowerCase().includes("left") ? "←" : m.toLowerCase().includes("up") ? "↑" : "↓")
            .replace(/\$\\(?:Right|Left)arrow\$/g, (m) => m.includes("Right") ? "⇒" : "⇐")
            .replace(/\\(?:Right|Left)arrow/g, (m) => m.includes("Right") ? "⇒" : "⇐")
            .replace(/\$\\to\$/g, "→")
            .replace(/\\to\b/g, "→")
            .replace(/-->/g, "→")
            .replace(/->/g, "→")
            .replace(/==>/g, "⇒")
            .replace(/=>/g, "⇒");

        // Split into lines/paragraphs
        const lines = cleanedText.split("\n");
        let activeList = null;

        lines.forEach((line) => {
            const trimmed = line.trim();

            // Handle bullet list items (starting with * or - or •)
            if (trimmed.startsWith("* ") || trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
                if (!activeList) {
                    activeList = document.createElement("ul");
                    container.appendChild(activeList);
                }
                const li = document.createElement("li");
                // Remove the bullet prefix
                const contentText = line.replace(/^\s*[\*\-•]\s*/, "");
                renderLineInlineFormatting(li, contentText);
                activeList.appendChild(li);
                return;
            }

            // If it was a list item but this line is not, close active list
            activeList = null;

            // Handle empty lines (paragraphs separator)
            if (trimmed === "") {
                const br = document.createElement("br");
                container.appendChild(br);
                return;
            }

            // Normal paragraph line
            const p = document.createElement("p");
            p.className = "message-paragraph";
            renderLineInlineFormatting(p, line);
            container.appendChild(p);
        });
    }

    /**
     * Parse inline formatting (like bold **) and append to element.
     */
    function renderLineInlineFormatting(element, lineText) {
        // Split by "**" to parse bold blocks
        const parts = lineText.split("**");
        parts.forEach((part, index) => {
            if (index % 2 === 1) {
                // Odd index: bold text
                const strong = document.createElement("strong");
                strong.textContent = part;
                element.appendChild(strong);
            } else {
                // Even index: regular text
                const textNode = document.createTextNode(part);
                element.appendChild(textNode);
            }
        });
    }

    /**
     * Append source citations to a message.
     */
    function appendSources(contentWrapper, sources) {
        const sourcesDiv = document.createElement("div");
        sourcesDiv.className = "sources-section";

        const title = document.createElement("div");
        title.className = "sources-title";
        title.textContent = "Sources";
        sourcesDiv.appendChild(title);

        sources.forEach((source) => {
            const chip = document.createElement("span");
            chip.className = "source-chip";
            chip.textContent = `📄 ${source.filename || "Unknown"} (${(source.score * 100).toFixed(0)}%)`;
            sourcesDiv.appendChild(chip);
        });

        contentWrapper.appendChild(sourcesDiv);
    }

    function appendComparison(container, comparison) {
        if (!container) return;
        const section = document.createElement("div"); section.className = "comparison-section";
        const title = document.createElement("strong");
        title.textContent = `Comparison: ${JSON.stringify(comparison.summary || {})}`;
        section.appendChild(title);
        const table = document.createElement("table"), head = document.createElement("tr");
        for (const label of ["Field", "PDF A (page)", "PDF B (page)", "Status"]) {
            const th = document.createElement("th"); th.textContent = label; head.appendChild(th);
        }
        table.appendChild(head);
        for (const row of comparison.results || []) {
            if (row.status === "equal") continue;
            const tr = document.createElement("tr"), a = (row.left || [])[0], b = (row.right || [])[0];
            for (const value of [row.key, a ? `${a.value} (p.${a.page})` : "—",
                                 b ? `${b.value} (p.${b.page})` : "—", row.status]) {
                const td = document.createElement("td"); td.textContent = String(value || ""); tr.appendChild(td);
            }
            table.appendChild(tr);
        }
        section.appendChild(table); container.appendChild(section);
    }

    function createTypingIndicator() {
        const wrapper = document.createElement("div");
        wrapper.className = "message assistant";

        const avatar = document.createElement("div");
        avatar.className = "message-avatar";
        avatar.textContent = "🤖";

        const content = document.createElement("div");
        content.className = "message-content";

        const indicator = document.createElement("div");
        indicator.className = "typing-indicator";

        for (let i = 0; i < 3; i++) {
            const dot = document.createElement("div");
            dot.className = "typing-dot";
            indicator.appendChild(dot);
        }

        content.appendChild(indicator);
        wrapper.appendChild(avatar);
        wrapper.appendChild(content);

        return wrapper;
    }

    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // -----------------------------------------------------------------------
    // Documents
    // -----------------------------------------------------------------------

    // Upload zone click
    uploadBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    uploadZone.addEventListener("click", () => {
        fileInput.click();
    });

    // Drag and drop
    uploadZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadZone.classList.add("drag-over");
    });

    uploadZone.addEventListener("dragleave", () => {
        uploadZone.classList.remove("drag-over");
    });

    uploadZone.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadZone.classList.remove("drag-over");
        const files = Array.from(e.dataTransfer.files);
        uploadFiles(files);
    });

    fileInput.addEventListener("change", () => {
        const files = Array.from(fileInput.files);
        uploadFiles(files);
        fileInput.value = ""; // Reset input
    });

    refreshDocsBtn.addEventListener("click", loadDocuments);

    if (syncSharepointBtn) {
        syncSharepointBtn.addEventListener("click", async () => {
            syncSharepointBtn.disabled = true;
            const originalText = syncSharepointBtn.textContent;
            syncSharepointBtn.textContent = "⏳ Syncing...";
            try {
                await ApiClient.postJSON("/onedrive/sync", {});
                alert("SharePoint sync started in the background. It may take a few minutes for documents to appear.");
            } catch (error) {
                if (error.message === "O365_AUTH_REQUIRED") {
                    try {
                        const authData = await ApiClient.getJSON("/onedrive/auth-url");
                        if (authData.url) {
                            window.location.href = authData.url;
                            return; // Stop execution to redirect
                        }
                    } catch (authError) {
                        alert("Failed to get Microsoft login URL.");
                    }
                } else {
                    alert(error.message || "Failed to trigger sync. Make sure Azure credentials are configured.");
                }
            } finally {
                syncSharepointBtn.disabled = false;
                syncSharepointBtn.textContent = originalText;
            }
        });
    }

    async function uploadFiles(files) {
        for (const file of files) {
            const progressItem = createProgressItem(file.name);
            uploadProgressList.appendChild(progressItem);

            try {
                const bar = progressItem.querySelector(".progress-bar");
                const statusEl = progressItem.querySelector(".progress-status");

                bar.style.width = "50%";
                statusEl.textContent = "Uploading...";

                await ApiClient.uploadFile("/documents/upload", file);

                bar.style.width = "100%";
                bar.classList.add("complete");
                statusEl.textContent = "Done";

                // Auto-refresh document list
                setTimeout(loadDocuments, 1000);
            } catch (error) {
                const bar = progressItem.querySelector(".progress-bar");
                const statusEl = progressItem.querySelector(".progress-status");
                bar.classList.add("error");
                statusEl.textContent = "Failed";

                if (error.message && (error.message.includes("401") || error.message.includes("403") || error.message.toLowerCase().includes("forbidden"))) {
                    sessionStorage.removeItem("rag_api_key");
                    apiKeyModal.classList.remove("hidden");
                }
            }
        }
    }

    function createProgressItem(filename) {
        const item = document.createElement("div");
        item.className = "upload-progress-item";

        const nameSpan = document.createElement("span");
        nameSpan.className = "progress-filename";
        nameSpan.textContent = filename;

        const barWrapper = document.createElement("div");
        barWrapper.className = "progress-bar-wrapper";

        const bar = document.createElement("div");
        bar.className = "progress-bar";
        barWrapper.appendChild(bar);

        const statusSpan = document.createElement("span");
        statusSpan.className = "progress-status";
        statusSpan.textContent = "Queued";

        item.appendChild(nameSpan);
        item.appendChild(barWrapper);
        item.appendChild(statusSpan);

        return item;
    }

    async function loadDocuments() {
        try {
            const data = await ApiClient.getJSON("/documents");
            renderDocumentList(data.documents || []);
            refreshPdfSelectors(data.documents || []);
        } catch (error) {
            // Show empty state on error
            renderDocumentList([]);

            if (error.message && (error.message.includes("401") || error.message.includes("403") || error.message.toLowerCase().includes("forbidden"))) {
                sessionStorage.removeItem("rag_api_key");
                apiKeyModal.classList.remove("hidden");
            }
        }
    }

    function refreshPdfSelectors(documents) {
        for (const [select, label] of [[compareLeft, "Select first PDF"], [compareRight, "Select second PDF"]]) {
            const current = select.value; select.replaceChildren();
            const empty = document.createElement("option"); empty.value = ""; empty.textContent = label; select.appendChild(empty);
            for (const doc of documents) {
                if (doc.file_type !== "pdf" || doc.status !== "completed") continue;
                const option = document.createElement("option"); option.value = doc.document_id;
                option.textContent = doc.filename; select.appendChild(option);
            }
            if ([...select.options].some(option => option.value === current)) select.value = current;
        }
    }

    function renderDocumentList(documents) {
        // Clear existing content safely
        documentList.replaceChildren();

        if (documents.length === 0) {
            const emptyDiv = document.createElement("div");
            emptyDiv.className = "empty-state";

            const icon = document.createElement("span");
            icon.className = "empty-icon";
            icon.textContent = "📭";

            const msg = document.createElement("p");
            msg.textContent = "No documents uploaded yet. Upload your first document to get started.";

            emptyDiv.appendChild(icon);
            emptyDiv.appendChild(msg);
            documentList.appendChild(emptyDiv);
            return;
        }

        const typeIcons = {
            pdf: "📕", docx: "📘", pptx: "📙", xlsx: "📗", txt: "📄", md: "📝",
        };

        documents.forEach((doc) => {
            const card = document.createElement("div");
            card.className = "doc-card";

            // Info section
            const infoDiv = document.createElement("div");
            infoDiv.className = "doc-info";

            const iconDiv = document.createElement("div");
            iconDiv.className = `doc-type-icon ${doc.file_type}`;
            iconDiv.textContent = typeIcons[doc.file_type] || "📄";

            const detailsDiv = document.createElement("div");
            detailsDiv.className = "doc-details";

            const nameDiv = document.createElement("div");
            nameDiv.className = "doc-name";
            nameDiv.textContent = doc.filename;

            const metaDiv = document.createElement("div");
            metaDiv.className = "doc-meta";

            const sizeSpan = document.createElement("span");
            sizeSpan.textContent = formatBytes(doc.file_size_bytes);

            const chunksSpan = document.createElement("span");
            chunksSpan.textContent = `${doc.chunk_count} chunks`;

            metaDiv.appendChild(sizeSpan);
            metaDiv.appendChild(chunksSpan);
            detailsDiv.appendChild(nameDiv);
            detailsDiv.appendChild(metaDiv);

            infoDiv.appendChild(iconDiv);
            infoDiv.appendChild(detailsDiv);

            // Status
            const statusDiv = document.createElement("div");
            statusDiv.className = "doc-status";

            const badge = document.createElement("span");
            badge.className = `status-badge ${doc.status}`;
            badge.textContent = doc.status;
            statusDiv.appendChild(badge);

            // Actions
            const actionsDiv = document.createElement("div");
            actionsDiv.className = "doc-actions";

            const deleteBtn = document.createElement("button");
            deleteBtn.className = "delete-btn";
            deleteBtn.textContent = "🗑";
            deleteBtn.title = "Delete document";
            deleteBtn.addEventListener("click", async () => {
                try {
                    await ApiClient.deleteRequest(`/documents/${doc.document_id}`);
                    card.remove();
                } catch {
                    // Silently handle error
                }
            });
            actionsDiv.appendChild(deleteBtn);

            card.appendChild(infoDiv);
            card.appendChild(statusDiv);
            card.appendChild(actionsDiv);
            documentList.appendChild(card);
        });
    }

    function formatBytes(bytes) {
        if (bytes === 0) return "0 B";
        const k = 1024;
        const sizes = ["B", "KB", "MB", "GB"];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
    }

    // -----------------------------------------------------------------------
    // Logs
    // -----------------------------------------------------------------------

    logQueryBtn.addEventListener("click", queryLogs);

    async function queryLogs() {
        const params = {
            service: logServiceFilter.value || null,
            level: logLevelFilter.value || null,
            search: logSearch.value.trim() || null,
            limit: 100,
        };

        try {
            const data = await ApiClient.postJSON("/logs", params);
            renderLogTable(data.logs || []);
        } catch {
            renderLogTable([]);
        }
    }

    function renderLogTable(logs) {
        logTableBody.replaceChildren();

        if (logs.length === 0) {
            const row = document.createElement("tr");
            row.className = "empty-row";
            const cell = document.createElement("td");
            cell.setAttribute("colspan", "5");
            cell.textContent = "No log entries found.";
            row.appendChild(cell);
            logTableBody.appendChild(row);
            return;
        }

        logs.forEach((log) => {
            const row = document.createElement("tr");

            // Timestamp
            const tsCell = document.createElement("td");
            tsCell.textContent = new Date(log.timestamp).toLocaleString();
            row.appendChild(tsCell);

            // Service
            const svcCell = document.createElement("td");
            svcCell.textContent = log.service;
            row.appendChild(svcCell);

            // Level
            const lvlCell = document.createElement("td");
            const lvlSpan = document.createElement("span");
            lvlSpan.className = `log-level ${log.level}`;
            lvlSpan.textContent = log.level;
            lvlCell.appendChild(lvlSpan);
            row.appendChild(lvlCell);

            // Message
            const msgCell = document.createElement("td");
            msgCell.className = "log-message";
            msgCell.textContent = log.message;
            row.appendChild(msgCell);

            // Request ID
            const reqCell = document.createElement("td");
            reqCell.className = "log-request-id";
            reqCell.textContent = log.request_id || "—";
            row.appendChild(reqCell);

            logTableBody.appendChild(row);
        });
    }
});
