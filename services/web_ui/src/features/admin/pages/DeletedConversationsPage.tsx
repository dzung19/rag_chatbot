import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, RotateCcw, Search } from "lucide-react";
import { fetchDeletedConversations, restoreConversation } from "../../../api/mockAdminApi";
import { RestoreDialog } from "../components/RestoreDialog";
import { useHasPermission } from "../permissions";
import { RETENTION_DAYS, type DeletedConversation } from "../types";
import { useToastStore } from "../../../stores/toastStore";
import styles from "../components/Admin.module.css";

function daysLeft(deletedAt: string): number {
  const expires = new Date(deletedAt).getTime() + RETENTION_DAYS * 86_400_000;
  return Math.max(0, Math.ceil((expires - Date.now()) / 86_400_000));
}

const formatDate = (iso: string) => new Date(iso).toLocaleString("vi-VN");

export function DeletedConversationsPage() {
  const canRestore = useHasPermission("conversation.restore");

  const [items, setItems] = useState<DeletedConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<DeletedConversation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setItems(await fetchDeletedConversations());
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Không tải được dữ liệu.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter(
      (c) => !q || c.title.toLowerCase().includes(q) || c.owner_name.toLowerCase().includes(q),
    );
  }, [items, search]);

  async function handleConfirm(reason: string) {
    if (!target) return;
    setSubmitting(true);
    setRestoreError(null);
    try {
      await restoreConversation(target.id, reason);
      setItems((prev) => prev.filter((c) => c.id !== target.id));
      useToastStore.getState().pushToast(`Đã khôi phục "${target.title}"`, "success");
      setTarget(null);
    } catch (e) {
      setRestoreError(e instanceof Error ? e.message : "Khôi phục thất bại.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <div className={styles.pageHeader}>
        <div>
          <h2>Chat đã xóa</h2>
          <p className={styles.muted}>
            Chat bị xóa vĩnh viễn sau {RETENTION_DAYS} ngày kể từ khi người dùng xóa.
          </p>
        </div>
        <button type="button" className={styles.secondaryButton} onClick={() => void load()} disabled={loading}>
          <RefreshCw size={16} aria-hidden="true" /> Làm mới
        </button>
      </div>

      <div className={styles.searchBox}>
        <Search size={16} aria-hidden="true" />
        <input
          type="search"
          placeholder="Tìm theo tiêu đề hoặc người dùng..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Tìm kiếm"
        />
      </div>

      {loading && <p className={styles.muted}>Đang tải...</p>}
      {loadError && <p className={styles.errorText} role="alert">{loadError}</p>}

      {!loading && !loadError && filtered.length === 0 && (
        <div className={styles.empty}>Không có cuộc trò chuyện nào đã xóa.</div>
      )}

      {!loading && filtered.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Tiêu đề</th>
                <th>Người sở hữu</th>
                <th>Ngày xóa</th>
                <th>Còn lại</th>
                <th>Số tin nhắn</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const left = daysLeft(c.deleted_at);
                return (
                  <tr key={c.id}>
                    <td className={styles.titleCell}>{c.title}</td>
                    <td>{c.owner_name}</td>
                    <td>{formatDate(c.deleted_at)}</td>
                    <td>
                      <span className={left <= 3 ? `${styles.badge} ${styles.badgeDanger}` : styles.badge}>
                        {left} ngày
                      </span>
                    </td>
                    <td>{c.message_count}</td>
                    <td className={styles.actionCell}>
                      {canRestore && (
                        <button
                          type="button"
                          className={styles.linkButton}
                          onClick={() => {
                            setRestoreError(null);
                            setTarget(c);
                          }}
                        >
                          <RotateCcw size={14} aria-hidden="true" /> Khôi phục
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {target && (
        <RestoreDialog
          conversation={target}
          submitting={submitting}
          error={restoreError}
          onCancel={() => setTarget(null)}
          onConfirm={(reason) => void handleConfirm(reason)}
        />
      )}
    </section>
  );
}
