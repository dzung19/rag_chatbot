import React, { useEffect, useState, useRef } from "react";
import {
  CloudUpload,
  CloudDownload,
  RefreshCw,
  Trash2,
  Inbox,
  LoaderCircle,
} from "lucide-react";
import { useDocumentsStore } from "../../stores/documentsStore";
import { FileTypeIcon } from "../../components/FileTypeIcon";
import { formatBytes } from "../../lib/formatBytes";
import styles from "./DocumentsView.module.css";

export const DocumentsView: React.FC = () => {
  const {
    documents,
    isLoading,
    isSyncing,
    uploads,
    fetchDocuments,
    uploadFiles,
    deleteDocument,
    syncSharePoint,
  } = useDocumentsStore();

  const [search, setSearch] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      uploadFiles(Array.from(e.target.files));
      e.target.value = "";
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      uploadFiles(Array.from(e.dataTransfer.files));
    }
  };

  const filteredDocs = documents.filter((doc) =>
    doc.filename.toLowerCase().includes(search.toLowerCase().trim())
  );

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.headerRow}>
        <div className={styles.titleGroup}>
          <h1>Document Management</h1>
          <p>Upload and manage knowledge base documents for AI retrieval</p>
        </div>

        <div className={styles.actionBtns}>
          <button
            className={styles.syncBtn}
            onClick={syncSharePoint}
            disabled={isSyncing}
            title="Synchronize from SharePoint / OneDrive"
          >
            {isSyncing ? (
              <LoaderCircle size={16} className="animate-spin" />
            ) : (
              <CloudDownload size={16} />
            )}
            <span>{isSyncing ? "Syncing..." : "Sync SharePoint"}</span>
          </button>

          <button
            className={styles.refreshBtn}
            onClick={() => fetchDocuments()}
            disabled={isLoading}
            title="Refresh documents list"
          >
            <RefreshCw size={16} className={isLoading ? "animate-spin" : ""} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Upload Zone */}
      <div
        className={`${styles.dropZone} ${isDragOver ? styles.dragOver : ""}`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.pptx,.xlsx,.txt,.md"
          multiple
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
        <CloudUpload size={36} className={styles.dropIcon} />
        <span className={styles.dropTitle}>
          Drop files here or click to browse
        </span>
        <span className={styles.dropSubtitle}>
          Supports PDF, DOCX, PPTX, XLSX, TXT, Markdown (up to 50MB per file)
        </span>
      </div>

      {/* Progress Queue */}
      {uploads.length > 0 && (
        <div className={styles.progressList}>
          {uploads.slice(0, 5).map((item) => (
            <div key={item.id} className={styles.progressItem}>
              <span className={styles.progressName}>{item.filename}</span>
              <div className={styles.progressBarTrack}>
                <div
                  className={styles.progressBarFill}
                  style={{ width: `${item.progress}%` }}
                />
              </div>
              <span
                style={{
                  fontSize: "var(--text-xs)",
                  color:
                    item.status === "failed"
                      ? "var(--badge-red-text)"
                      : item.status === "done"
                      ? "var(--badge-green-text)"
                      : "var(--text-muted)",
                }}
              >
                {item.status === "done"
                  ? "Done"
                  : item.status === "failed"
                  ? "Failed"
                  : "Uploading..."}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Table Section */}
      <div className={styles.tableHeader}>
        <h2 style={{ fontSize: "var(--text-base)", fontWeight: 600 }}>
          Indexed Documents ({documents.length})
        </h2>
        <input
          type="text"
          className={styles.searchInput}
          placeholder="Filter documents..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Document</th>
              <th>Size</th>
              <th>Chunks</th>
              <th>Status</th>
              <th style={{ width: "40px" }}></th>
            </tr>
          </thead>
          <tbody>
            {filteredDocs.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <div className={styles.emptyState}>
                    <Inbox size={32} />
                    <span>
                      {search
                        ? "No documents match your search"
                        : "No documents indexed yet. Upload your first file above."}
                    </span>
                  </div>
                </td>
              </tr>
            ) : (
              filteredDocs.map((doc) => (
                <tr key={doc.document_id}>
                  <td>
                    <div className={styles.docNameCell}>
                      <FileTypeIcon type={doc.file_type} size={16} />
                      <span>{doc.filename}</span>
                    </div>
                  </td>
                  <td>{formatBytes(doc.file_size_bytes)}</td>
                  <td>{doc.chunk_count} chunks</td>
                  <td>
                    <span className={`${styles.statusBadge} ${styles[doc.status]}`}>
                      {doc.status}
                    </span>
                  </td>
                  <td>
                    <button
                      className={styles.deleteBtn}
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete "${doc.filename}" from the knowledge base?`
                          )
                        ) {
                          deleteDocument(doc.document_id);
                        }
                      }}
                      title="Delete document"
                      aria-label="Delete document"
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
