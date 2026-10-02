/**
 * API Client Module for RAG Chatbot.
 *
 * Centralized fetch wrapper with API key injection and error handling.
 * Security: API key sent via X-API-Key header only, never in URL params.
 */

const ApiClient = (() => {
    "use strict";

    let _apiKey = "";
    // Base URL for API requests
    // Uses the actual hostname (LAN IP) and port 8000 to reach the Gateway directly
    const BASE_URL = window.location.port === "3000"
        ? `http://${window.location.hostname}:8000/api/v1`
        : "/api/v1";

    /**
     * Set the API key for all subsequent requests.
     * @param {string} key
     */
    function setApiKey(key) {
        _apiKey = key;
    }

    /**
     * Get the current API key.
     * @returns {string}
     */
    function getApiKey() {
        return _apiKey;
    }

    /**
     * Make an authenticated fetch request.
     * @param {string} path — API path (e.g., "/chat/sync")
     * @param {Object} options — fetch options
     * @returns {Promise<Response>}
     */
    async function request(path, options = {}) {
        const url = `${BASE_URL}${path}`;
        const headers = {
            "X-API-Key": _apiKey,
            ...(options.headers || {}),
        };

        // Don't set Content-Type for FormData (browser sets it with boundary)
        if (!(options.body instanceof FormData)) {
            headers["Content-Type"] = headers["Content-Type"] || "application/json";
        }

        const response = await fetch(url, {
            ...options,
            headers,
            cache: options.cache || "no-store", // Prevent browser caching by default
        });

        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                errorMsg = errorData.detail || errorMsg;
            } catch {
                // Ignore JSON parse errors
            }
            throw new Error(errorMsg);
        }

        return response;
    }

    /**
     * POST JSON request.
     * @param {string} path
     * @param {Object} data
     * @returns {Promise<Object>}
     */
    async function postJSON(path, data) {
        const response = await request(path, {
            method: "POST",
            body: JSON.stringify(data),
        });
        return response.json();
    }

    /**
     * GET JSON request.
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async function getJSON(path) {
        const response = await request(path);
        return response.json();
    }

    /**
     * DELETE request.
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async function deleteRequest(path) {
        const response = await request(path, { method: "DELETE" });
        return response.json();
    }

    /**
     * Upload a file via multipart form.
     * @param {string} path
     * @param {File} file
     * @returns {Promise<Object>}
     */
    async function uploadFile(path, file) {
        const formData = new FormData();
        formData.append("file", file);

        const response = await request(path, {
            method: "POST",
            body: formData,
        });
        return response.json();
    }

    /**
     * Create an SSE connection for streaming chat.
     * @param {string} path
     * @param {Object} data — request body
     * @param {Object} callbacks — { onToken, onSources, onDone, onError }
     * @returns {Promise<void>}
     */
    async function streamChat(path, data, callbacks) {
        const response = await request(path, {
            method: "POST",
            body: JSON.stringify(data),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (line.startsWith("event: ")) {
                    const eventType = line.slice(7).trim();
                    // Next line should be data
                    continue;
                }
                if (line.startsWith("data: ")) {
                    const dataStr = line.slice(6);
                    // Determine event type from previous event line
                    // SSE parsing: look at the accumulated state
                    if (callbacks.onToken) {
                        callbacks.onToken(dataStr);
                    }
                }
            }
        }

        if (callbacks.onDone) {
            callbacks.onDone();
        }
    }

    /**
     * Stream chat using EventSource-like manual parsing.
     * @param {Object} data — { query, top_k, temperature }
     * @param {Object} callbacks — { onToken, onSources, onDone, onError }
     */
    async function streamChatQuery(data, callbacks) {
        try {
            const response = await request("/chat", {
                method: "POST",
                body: JSON.stringify(data),
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let eventType = "";
            let dataParts = [];

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (let line of lines) {
                    if (line.endsWith("\r")) {
                        line = line.slice(0, -1);
                    }

                    if (line.trim() === "") {
                        // Empty line denotes end of an SSE message block — dispatch accumulated event
                        if (eventType && dataParts.length > 0) {
                            const dataStr = dataParts.join("\n");
                            if (eventType === "sources" && callbacks.onSources) {
                                try {
                                    callbacks.onSources(JSON.parse(dataStr));
                                } catch { /* ignore parse errors */ }
                            } else if (eventType === "token" && callbacks.onToken) {
                                try {
                                    callbacks.onToken(JSON.parse(dataStr));
                                } catch {
                                    callbacks.onToken(dataStr);
                                }
                            } else if (eventType === "done" && callbacks.onDone) {
                                callbacks.onDone();
                            } else if (eventType === "error" && callbacks.onError) {
                                callbacks.onError(dataStr);
                            }
                        }
                        eventType = "";
                        dataParts = [];
                    } else if (line.startsWith("event:")) {
                        let eventVal = line.slice(6);
                        if (eventVal.startsWith(" ")) {
                            eventVal = eventVal.slice(1);
                        }
                        eventType = eventVal;
                    } else if (line.startsWith("data:")) {
                        let dataVal = line.slice(5);
                        if (dataVal.startsWith(" ")) {
                            dataVal = dataVal.slice(1);
                        }
                        dataParts.push(dataVal);
                    }
                }
            }

            // Flush any remaining data parts after stream closes
            if (eventType && dataParts.length > 0) {
                const dataStr = dataParts.join("\n");
                if (eventType === "sources" && callbacks.onSources) {
                    try {
                        callbacks.onSources(JSON.parse(dataStr));
                    } catch { /* ignore */ }
                } else if (eventType === "token" && callbacks.onToken) {
                    try {
                        callbacks.onToken(JSON.parse(dataStr));
                    } catch {
                        callbacks.onToken(dataStr);
                    }
                } else if (eventType === "done" && callbacks.onDone) {
                    callbacks.onDone();
                } else if (eventType === "error" && callbacks.onError) {
                    callbacks.onError(dataStr);
                }
            }

            // Ensure done is called even if no explicit done event
            if (callbacks.onDone) {
                callbacks.onDone();
            }
        } catch (error) {
            if (callbacks.onError) {
                callbacks.onError(error.message);
            }
        }
    }

    /**
     * Check API health.
     * @returns {Promise<Object>}
     */
    async function healthCheck() {
        const response = await fetch(`${BASE_URL}/health`);
        if (!response.ok) throw new Error("Health check failed");
        return response.json();
    }

    return {
        setApiKey,
        getApiKey,
        postJSON,
        getJSON,
        deleteRequest,
        uploadFile,
        streamChatQuery,
        healthCheck,
    };
})();
