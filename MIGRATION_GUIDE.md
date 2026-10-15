# Hướng Dẫn Di Chuyển Cơ Sở Dữ Liệu: SQLite sang PostgreSQL

Tài liệu này hướng dẫn chi tiết quy trình di chuyển toàn bộ dữ liệu từ các file SQLite rời rạc (`shared_data/shared_db.db`, `shared_data/chat_history.db`) sang cơ sở dữ liệu **PostgreSQL** tập trung, chuẩn hóa 100% theo bản vẽ kiến trúc [rag_chatbot_database.svg](file:///c:/Users/phung/New%20folder/rag_chatbot/rag_chatbot_database.svg).

---

## 1. Tổng quan & Phạm vi dữ liệu

Hệ thống chuyển đổi 4 nhóm dữ liệu chính từ SQLite sang PostgreSQL:

| Nhóm dữ liệu | Bảng SQLite nguồn | Bảng PostgreSQL đích | Mục đích sử dụng |
|---|---|---|---|
| **Skills Catalog** | `skills.db` / `shared_db.db` | `skills` | Quản lý System Skills & Custom Skills của Chatbot |
| **Chat History** | `chat_history.db` | `conversations`, `chat_turns`, `messages`, `admin_audit_logs` | Quản lý lịch sử phiên chat, tin nhắn và nguồn trích dẫn |
| **Full Documents** | `shared_db.db` (`full_documents`) | `full_documents` | Toàn văn tài liệu cho tính năng So sánh nghiệp vụ (`business-compare`) |
| **Document Chunks** | `shared_db.db` (`document_chunks` FTS5) | `document_chunks` (tsvector GIN) | Chunks văn bản phục vụ Vector Search & BM25 Full-Text Search tiếng Việt |

### Cơ chế an toàn (Zero-Downtime & Fallback)
Mã nguồn của hệ thống đã được cấu hình theo nguyên tắc **Postgres-First with SQLite Fallback**:
- Hệ thống luôn ưu tiên đọc và ghi vào **PostgreSQL**.
- Nếu PostgreSQL tạm thời chưa khả dụng hoặc bản ghi cũ chưa được migrate, hệ thống tự động fallback đọc từ **SQLite**.
- Quá trình upload tài liệu mới tự động **Dual-Write** sang cả 2 bên cho đến khi bạn quyết định gỡ bỏ hoàn toàn SQLite.

---

## 2. Chuẩn bị trước khi chạy (Prerequisites)

1. Đảm bảo Docker Compose đang chạy các dịch vụ, đặc biệt là container `postgres`:
   ```bash
   docker compose ps postgres
   ```
2. Nếu container `postgres` chưa bật, khởi động bằng lệnh:
   ```bash
   docker compose up -d postgres
   ```
3. Đảm bảo cấu hình biến môi trường kết nối PostgreSQL trong file `.env` hoặc mặc định:
   ```env
   DATABASE_URL=postgresql+psycopg://rag_user:rag_password@postgres:5432/rag_chatbot
   ```

---

## 3. Quy trình thực hiện Migration

### Cách 1: Chạy tự động toàn bộ 1-Bước qua Docker (Khuyến nghị)
Đây là cách nhanh nhất và an toàn nhất vì môi trường container `ingestion` đã có sẵn mọi thư viện Python cần thiết (`sqlalchemy`, `psycopg`).

Chạy lệnh sau trên terminal của máy chủ/máy host:
```bash
docker compose exec ingestion python scripts/migrate_all_sqlite_to_pg.py
```

Tiến trình sẽ tự động thực hiện tuần tự 4 bước:
1. `Step 1/4`: Khởi tạo bảng `skills` và seed 10 base skills mặc định vào PostgreSQL.
2. `Step 2/4`: Di chuyển toàn bộ bảng hội thoại từ `chat_history.db` sang PostgreSQL.
3. `Step 3/4`: Di chuyển bảng `full_documents` từ `shared_db.db` sang PostgreSQL.
4. `Step 4/4`: Di chuyển `document_chunks` từ SQLite FTS5 sang bảng PostgreSQL có chỉ mục Full-Text `GIN(tsv)`.

---

### Cách 2: Chạy trực tiếp từ máy Host (Local Python)
Nếu bạn muốn chạy script trực tiếp trên máy host Windows mà không thông qua Docker exec:

1. Cài đặt các thư viện kết nối cơ sở dữ liệu:
   ```bash
   pip install sqlalchemy psycopg[binary]
   ```
2. Chạy lệnh:
   ```bash
   python scripts/migrate_all_sqlite_to_pg.py --pg-url "postgresql+psycopg://rag_user:rag_password@localhost:5432/rag_chatbot"
   ```

---

### Cách 3: Chạy từng script riêng lẻ (Tùy chọn kiểm soát chi tiết)

Nếu muốn di chuyển từng phần riêng biệt hoặc kiểm tra logs chi tiết:

#### 1. Di chuyển lịch sử chat:
```bash
docker compose exec conversation-service python scripts/migrate_sqlite_to_pg_conversations.py
```

#### 2. Di chuyển toàn văn tài liệu (Full Documents):
```bash
docker compose exec ingestion python scripts/migrate_sqlite_to_pg_full_documents.py
```

#### 3. Di chuyển Chunks & Full-Text Search:
```bash
docker compose exec ingestion python scripts/migrate_sqlite_to_pg_chunks.py
```

---

## 4. Kiểm tra & Xác minh sau Migration (Verification)

### 4.1. Kiểm tra trực tiếp trong PostgreSQL
Bạn có thể kết nối vào PostgreSQL để kiểm tra số lượng bản ghi đã được sao chép thành công:

```bash
docker compose exec postgres psql -U rag_user -d rag_chatbot -c "
SELECT 'skills' AS table_name, count(*) AS total_rows FROM skills
UNION ALL
SELECT 'conversations', count(*) FROM conversations
UNION ALL
SELECT 'messages', count(*) FROM messages
UNION ALL
SELECT 'full_documents', count(*) FROM full_documents
UNION ALL
SELECT 'document_chunks', count(*) FROM document_chunks;
"
```

### 4.2. Thử nghiệm tìm kiếm Full-Text Search tiếng Việt (BM25)
Chạy thử câu lệnh SQL kiểm tra khả năng tìm kiếm không dấu (`unaccent`):
```bash
docker compose exec postgres psql -U rag_user -d rag_chatbot -c "
SELECT id, filename, ts_rank_cd(tsv, plainto_tsquery('simple', unaccent('hop dong'))) AS rank_score
FROM document_chunks
WHERE tsv @@ plainto_tsquery('simple', unaccent('hop dong'))
ORDER BY rank_score DESC
LIMIT 5;
"
```

### 4.3. Kiểm tra trải nghiệm trên giao diện Web UI (Port 3000)
1. **Lịch sử chat**: Mở trình duyệt tại `http://localhost:3000`, xác nhận danh sách các đoạn chat cũ trên Sidebar vẫn hiển thị đầy đủ và có thể xem lại lịch sử tin nhắn.
2. **Kỹ năng (Skills)**: Vào phần chọn kỹ năng, kiểm tra 10 kỹ năng mặc định và các kỹ năng tùy biến tải bình thường.
3. **So sánh nghiệp vụ**: Thử tính năng `business-compare` giữa 2 file tài liệu đã tải lên, hệ thống sẽ đọc toàn văn trực tiếp từ PostgreSQL `full_documents`.
4. **Hỏi đáp RAG**: Gửi câu hỏi mới để xác nhận Hybrid Search (Vector + PostgreSQL Keyword Search) hoạt động ổn định và trả lời chính xác.

---

## 5. Dọn dẹp & Loại bỏ hoàn toàn SQLite (Post-Migration Phase-out)

Sau khi kiểm tra hệ thống hoạt động ổn định 100% trên PostgreSQL trong 1–3 ngày làm việc:

1. **Sao lưu các file SQLite cũ (Archive)**:
   ```bash
   mkdir -p backups/sqlite_archive
   cp shared_data/*.db backups/sqlite_archive/
   ```
2. **Gỡ bỏ code SQLite cũ**:
   - Có thể xóa file `shared/sqlite_db.py`.
   - Gỡ bỏ các hàm fallback SQLite trong `services/rag_engine/retriever.py` và `services/ingestion/main.py`.
3. **Giải phóng tài nguyên**:
   - Xóa volume `shared_data` trong `docker-compose.yml` nếu không còn dịch vụ nào cần lưu trữ file cục bộ ngoài thư mục `uploads/`.

---

## 6. Xử lý sự cố thường gặp (Troubleshooting)

### 1. Lỗi `Connection refused` khi kết nối PostgreSQL
- **Nguyên nhân**: Container `postgres` chưa kịp khởi động xong hoặc chưa healthy.
- **Khắc phục**: Chờ vài giây và kiểm tra log: `docker compose logs postgres --tail 50`. Đảm bảo port `5432` không bị chiếm dụng bởi ứng dụng khác.

### 2. Lỗi `ModuleNotFoundError: No module named 'psycopg'` khi chạy trên máy Host
- **Khắc phục**: Khuyến nghị chạy qua lệnh Docker: `docker compose exec ingestion python scripts/migrate_all_sqlite_to_pg.py`, hoặc chạy `pip install psycopg[binary] sqlalchemy` trên môi trường Python của máy host.

### 3. Có dữ liệu mới được thêm vào SQLite trong lúc chạy migration?
- Script migration sử dụng cú pháp `ON CONFLICT (...) DO UPDATE` hoặc `DO NOTHING`, cho phép bạn chạy lại script migration nhiều lần một cách an toàn mà không sợ trùng lặp khóa chính hay mất dữ liệu.
