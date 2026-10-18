import React, { useEffect, useState } from "react";
import { Search, ExternalLink, Copy, Check } from "lucide-react";
import { fetchLogs } from "../../api/endpoints";
import type { LogEntry } from "../../types/api";
import { useToastStore } from "../../stores/toastStore";
import styles from "./LogsView.module.css";

export const LogsView: React.FC = () => {
  const { pushToast } = useToastStore();

  const [service, setService] = useState("");
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasQueried, setHasQueried] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setIsLoading(true);
    setHasQueried(true);

    try {
      const data = await fetchLogs({
        service: service || null,
        level: level || null,
        search: search.trim() || null,
        limit: 100,
      });
      setLogs(data.logs || []);
    } catch (err: unknown) {
      setLogs([]);
      const msg = err instanceof Error ? err.message : "Failed to fetch logs";
      pushToast(msg, "error");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void handleSearch();
  }, []);

  const copyRequestId = async (id: string) => {
    try {
      await navigator.clipboard.writeText(id);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      // Ignore
    }
  };

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.headerRow}>
        <h1>Application Logs</h1>
        <p>Real-time audit log aggregation and trace inspection</p>
      </div>

      {/* Filters */}
      <form onSubmit={handleSearch} className={styles.filtersBar}>
        <div className={styles.filterGroup}>
          <label htmlFor="log-service">Service</label>
          <select
            id="log-service"
            className={styles.select}
            value={service}
            onChange={(e) => setService(e.target.value)}
          >
            <option value="">All Services</option>
            <option value="gateway">Gateway</option>
            <option value="ingestion">Ingestion</option>
            <option value="engine">RAG Engine</option>
            <option value="conversation">Conversation</option>
            <option value="ollama">Ollama</option>
          </select>
        </div>

        <div className={styles.filterGroup}>
          <label htmlFor="log-level">Level</label>
          <select
            id="log-level"
            className={styles.select}
            value={level}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option value="">All Levels</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </div>

        <div className={`${styles.filterGroup}`} style={{ flex: 1, minWidth: "180px" }}>
          <label htmlFor="log-search-input">Search Message</label>
          <input
            id="log-search-input"
            type="text"
            className={styles.input}
            placeholder="Filter message text..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <button
          type="submit"
          className={styles.searchBtn}
          disabled={isLoading}
        >
          <Search size={16} />
          <span>{isLoading ? "Searching..." : "Search Logs"}</span>
        </button>
      </form>

      {/* Table */}
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th style={{ width: "160px" }}>Timestamp</th>
              <th style={{ width: "110px" }}>Service</th>
              <th style={{ width: "90px" }}>Level</th>
              <th>Message</th>
              <th style={{ width: "140px" }}>Request ID</th>
            </tr>
          </thead>
          <tbody>
            {!hasQueried ? (
              <tr>
                <td colSpan={5} style={{ textAlign: "center", padding: "2.5rem", color: "var(--text-muted)" }}>
                  Click "Search Logs" to retrieve log entries
                </td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ textAlign: "center", padding: "2.5rem", color: "var(--text-muted)" }}>
                  No log entries found matching criteria
                </td>
              </tr>
            ) : (
              logs.map((log, index) => (
                <tr key={index}>
                  <td style={{ color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {new Date(log.timestamp).toLocaleString()}
                  </td>
                  <td>{log.service}</td>
                  <td>
                    <span className={`${styles.levelBadge} ${styles[log.level]}`}>
                      {log.level}
                    </span>
                  </td>
                  <td className={styles.messageCell}>{log.message}</td>
                  <td>
                    {log.request_id ? (
                      <span
                        className={styles.reqId}
                        onClick={() => copyRequestId(log.request_id!)}
                        title="Click to copy request ID"
                      >
                        <span>{log.request_id.slice(0, 8)}…</span>
                        {copiedId === log.request_id ? (
                          <Check size={12} color="var(--badge-green-text)" />
                        ) : (
                          <Copy size={12} />
                        )}
                      </span>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Grafana Integration Card */}
      <div className={styles.grafanaCard}>
        <div>
          <strong>Need deep log analytics?</strong> Loki & Promtail aggregate full
          system traces and metrics.
        </div>
        <a
          href="http://localhost:3001"
          target="_blank"
          rel="noopener noreferrer"
          className={styles.grafanaLink}
        >
          <span>Open Grafana</span>
          <ExternalLink size={15} />
        </a>
      </div>
    </div>
  );
};
