/**
 * Logs module handling log querying and rendering.
 */
window.initLogs = function() {
    "use strict";

    const logServiceFilter = document.getElementById("log-service-filter");
    const logLevelFilter = document.getElementById("log-level-filter");
    const logSearch = document.getElementById("log-search");
    const logQueryBtn = document.getElementById("log-query-btn");
    const logTableBody = document.getElementById("log-table-body");

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
};
