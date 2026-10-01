/**
 * Documents module handling file uploads and management.
 */
window.initDocuments = function() {
    "use strict";

    const uploadZone = document.getElementById("upload-zone");
    const fileInput = document.getElementById("file-input");
    const uploadBtn = document.getElementById("upload-btn");
    const uploadProgressList = document.getElementById("upload-progress-list");
    const documentList = document.getElementById("document-list");
    const refreshDocsBtn = document.getElementById("refresh-docs-btn");
    const syncSharepointBtn = document.getElementById("sync-sharepoint-btn");
    const apiKeyModal = document.getElementById("api-key-modal");

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

    refreshDocsBtn.addEventListener("click", window.loadDocuments);

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
                setTimeout(window.loadDocuments, 1000);
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

    window.loadDocuments = async function() {
        try {
            const data = await ApiClient.getJSON("/documents");
            renderDocumentList(data.documents || []);
        } catch (error) {
            // Show empty state on error
            renderDocumentList([]);

            if (error.message && (error.message.includes("401") || error.message.includes("403") || error.message.toLowerCase().includes("forbidden"))) {
                sessionStorage.removeItem("rag_api_key");
                apiKeyModal.classList.remove("hidden");
            }
        }
    };

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
};
