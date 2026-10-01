/**
 * Chat module handling message sending, receiving, and streaming.
 */
window.initChat = function() {
    "use strict";

    const chatMessages = document.getElementById("chat-messages");
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const charCount = document.getElementById("char-count");
    const welcomeMessage = document.getElementById("welcome-message");
    const chatAttachments = document.getElementById("chat-attachments");
    const chatFileInput = document.getElementById("chat-file-input");
    const attachBtn = document.getElementById("attach-btn");
    const chatInputWrapper = document.getElementById("chat-input-wrapper");
    const hintBtns = document.querySelectorAll(".hint-btn");
    const apiKeyModal = document.getElementById("api-key-modal");

    let isStreaming = false;
    let pendingChatFiles = [];

    // Auto-resize textarea
    chatInput.addEventListener("input", () => {
        chatInput.style.height = "auto";
        chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + "px";
        charCount.textContent = `${chatInput.value.length} / 5000`;
        updateSendButtonState();
    });

    function updateSendButtonState() {
        sendBtn.disabled = (chatInput.value.trim().length === 0 && pendingChatFiles.length === 0) || isStreaming;
    }

    // Chat Attachment Logic
    attachBtn.addEventListener("click", () => {
        chatFileInput.click();
    });

    chatFileInput.addEventListener("change", () => {
        handleChatFiles(Array.from(chatFileInput.files));
        chatFileInput.value = "";
    });

    chatInputWrapper.addEventListener("dragover", (e) => {
        e.preventDefault();
        chatInputWrapper.classList.add("drag-over");
    });

    chatInputWrapper.addEventListener("dragleave", () => {
        chatInputWrapper.classList.remove("drag-over");
    });

    chatInputWrapper.addEventListener("drop", (e) => {
        e.preventDefault();
        chatInputWrapper.classList.remove("drag-over");
        handleChatFiles(Array.from(e.dataTransfer.files));
    });

    function handleChatFiles(files) {
        const MAX_FILES = 2;
        const MAX_SIZE = 50 * 1024 * 1024; // 50MB
        const ALLOWED_EXTS = [".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md"];

        let added = false;
        for (const file of files) {
            if (pendingChatFiles.length >= MAX_FILES) {
                alert(`Maximum ${MAX_FILES} files allowed.`);
                break;
            }

            const ext = "." + file.name.split(".").pop().toLowerCase();
            if (!ALLOWED_EXTS.includes(ext)) {
                alert(`File type not supported: ${ext}`);
                continue;
            }

            if (file.size > MAX_SIZE) {
                alert(`File too large: ${file.name} (Max 50MB)`);
                continue;
            }

            pendingChatFiles.push(file);
            added = true;
        }

        if (added) {
            renderChatAttachments();
            updateSendButtonState();
        }
    }

    function renderChatAttachments() {
        chatAttachments.replaceChildren();
        pendingChatFiles.forEach((file, index) => {
            const chip = document.createElement("div");
            chip.className = "chat-attachment-chip";
            
            const nameSpan = document.createElement("span");
            nameSpan.textContent = file.name;
            
            const removeBtn = document.createElement("button");
            removeBtn.className = "remove-attachment";
            removeBtn.innerHTML = "×";
            removeBtn.title = "Remove";
            removeBtn.addEventListener("click", () => {
                pendingChatFiles.splice(index, 1);
                renderChatAttachments();
                updateSendButtonState();
            });
            
            chip.appendChild(nameSpan);
            chip.appendChild(removeBtn);
            chatAttachments.appendChild(chip);
        });
    }

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

    async function sendMessage() {
        const query = chatInput.value.trim();
        if ((!query && pendingChatFiles.length === 0) || isStreaming) return;

        // Hide welcome message
        if (welcomeMessage) {
            welcomeMessage.style.display = "none";
        }

        // Handle file uploads first if any
        if (pendingChatFiles.length > 0) {
            isStreaming = true;
            sendBtn.disabled = true;
            
            // Show a temporary message about uploading
            const uploadMsgEl = appendMessage("assistant", `Uploading ${pendingChatFiles.length} document(s)...`);
            scrollToBottom();

            try {
                for (const file of pendingChatFiles) {
                    await ApiClient.uploadFile("/documents/upload", file);
                }
                pendingChatFiles = [];
                renderChatAttachments();
                
                // Update the temporary message
                const contentEl = uploadMsgEl.querySelector(".message-text");
                if (contentEl) {
                    renderFormattedText(contentEl, "Documents uploaded successfully! Processing query...");
                }
            } catch (error) {
                const contentEl = uploadMsgEl.querySelector(".message-text");
                if (contentEl) {
                    renderFormattedText(contentEl, "Failed to upload documents: " + error.message);
                }
                isStreaming = false;
                updateSendButtonState();
                return;
            }
        }

        if (!query) {
            // If there was no text query, just stop after uploading
            isStreaming = false;
            updateSendButtonState();
            return;
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
        let messageEl = null;

        ApiClient.streamChatQuery(
            { query, top_k: 5, temperature: 0.7, stream: true },
            {
                onSources: (sources) => {
                    responseSources = sources;
                },
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
                    updateSendButtonState();

                    // Add sources if available
                    if (messageEl && responseSources.length > 0) {
                        const contentWrapper = messageEl.querySelector(".message-content");
                        if (contentWrapper) {
                            appendSources(contentWrapper, responseSources);
                        }
                    }

                    // If no response received, show fallback
                    if (!messageEl) {
                        appendMessage("assistant", responseText || "No response received. Please try again.");
                    }
                    scrollToBottom();
                },
                onError: (error) => {
                    if (typingEl.parentNode) {
                        typingEl.remove();
                    }
                    isStreaming = false;
                    updateSendButtonState();
                    
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

            if (trimmed.startsWith("* ") || trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
                if (!activeList) {
                    activeList = document.createElement("ul");
                    container.appendChild(activeList);
                }
                const li = document.createElement("li");
                const contentText = line.replace(/^\s*[\*\-•]\s*/, "");
                renderLineInlineFormatting(li, contentText);
                activeList.appendChild(li);
                return;
            }

            activeList = null;

            if (trimmed === "") {
                const br = document.createElement("br");
                container.appendChild(br);
                return;
            }

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
        const parts = lineText.split("**");
        parts.forEach((part, index) => {
            if (index % 2 === 1) {
                const strong = document.createElement("strong");
                strong.textContent = part;
                element.appendChild(strong);
            } else {
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
};
