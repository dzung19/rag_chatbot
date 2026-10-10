import { useEffect, useState } from "react";
import { fetchAuditLogs } from "../api/mockAdminApi";
import type { AuditLogEntry } from "../types";
import styles from "../components/Admin.module.css";

export function AuditLogPage() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAuditLogs()
      .then(setLogs)
      .finally(() => setLoading(false));
  }, []);

  return (
    <section>
      <div className={styles.pageHeader}>
        <div>
          <h2>Nhật ký quản trị</h2>
          <p className={styles.muted}>Mọi thao tác khôi phục đều được ghi lại.</p>
        </div>
      </div>

      {loading && <p className={styles.muted}>Đang tải...</p>}
      {!loading && logs.length === 0 && (
        <div className={styles.empty}>Chưa có thao tác nào. Thử khôi phục một chat ở tab "Chat đã xóa".</div>
      )}

      {!loading && logs.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Thời gian</th>
                <th>Quản trị viên</th>
                <th>Thao tác</th>
                <th>Đối tượng</th>
                <th>Lý do</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td>{new Date(log.created_at).toLocaleString("vi-VN")}</td>
                  <td>{log.actor_id}</td>
                  <td><span className={styles.badge}>Khôi phục</span></td>
                  <td className={styles.titleCell}>{log.target_title}</td>
                  <td>{log.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
