# Purge chat history

## Cài đặt
Copy `scripts/` và `tests/` vào `D:\rag_chatbot\`. Thêm vào `.gitignore`:

    logs/
    data/chat_history_backup_*.db
    data/purge.lock

## 1. Chạy test (DB tạm)
    $env:PYTHONPATH = "D:\rag_chatbot"
    .\venv\Scripts\python.exe -m pip install pytest
    .\venv\Scripts\python.exe -m pytest tests\test_purge.py -v

## 2. Dry-run (không xóa)
    .\venv\Scripts\python.exe scripts\purge_conversations.py --retention-days 30

## 3. Chạy thật thủ công (tự backup, phải gõ PURGE)
    .\venv\Scripts\python.exe scripts\purge_conversations.py --retention-days 30 --execute

## 4. Chạy qua wrapper (backup + purge + dọn backup cũ + log)
    .\scripts\run_purge.ps1 -RetentionDays 30 -BackupKeepDays 7

## 5. Đặt lịch (PowerShell Administrator, sau khi bước 4 ổn định)
    .\scripts\register_purge_task.ps1 -RetentionDays 30 -At "2:00AM"

Log: `logs\purge\`. LastTaskResult = 0 là thành công.
RetentionDays / BackupKeepDays phải theo quy định lưu trữ được phê duyệt.
