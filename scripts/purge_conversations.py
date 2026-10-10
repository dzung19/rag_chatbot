"""Purge (hard delete) soft-deleted conversations older than a retention period.

Mặc định chỉ chạy thử (dry-run). Dùng --execute để xóa thật.

Exit codes:
    0  Thành công (hoặc dry-run)
    1  Người dùng hủy / lỗi chung
    2  Số bản ghi vượt --max-delete
    3  Backup lỗi hoặc không vượt qua integrity_check
    4  Còn message mồ côi sau khi xóa
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from services.conversation_service.database import (  # noqa: E402
    DATABASE_URL,
    SessionLocal,
)
from services.conversation_service.models import (  # noqa: E402
    ChatTurn,
    Conversation,
    Message,
)


def _eligible_filter(cutoff: datetime):
    """Chỉ chat đã xóa mềm, quá thời hạn và không có turn đang generating."""
    active_turn = (
        select(ChatTurn.id)
        .where(
            ChatTurn.conversation_id == Conversation.id,
            ChatTurn.status == "generating",
        )
        .exists()
    )
    return (
        Conversation.deleted_at.is_not(None),
        Conversation.deleted_at <= cutoff,
        ~active_turn,
    )


def count_candidates(session, cutoff: datetime) -> int:
    stmt = select(func.count(Conversation.id)).where(*_eligible_filter(cutoff))
    return session.scalar(stmt) or 0


def find_candidates(session, cutoff: datetime, limit: int) -> list[str]:
    stmt = (
        select(Conversation.id)
        .where(*_eligible_filter(cutoff))
        .order_by(Conversation.deleted_at)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def count_orphans(session) -> int:
    stmt = select(func.count(Message.id)).where(
        ~select(Conversation.id)
        .where(Conversation.id == Message.conversation_id)
        .exists()
    )
    return session.scalar(stmt) or 0


def backup_sqlite() -> Path | None:
    """Sao lưu SQLite bằng backup API (nhất quán kể cả khi đang dùng WAL)."""
    url = make_url(DATABASE_URL)
    if url.get_backend_name() != "sqlite":
        print("Not SQLite: take a database backup with your DB tooling first.")
        return None

    db_path = Path(url.database).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{stamp}{db_path.suffix}")

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(backup_path)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()

    if result != "ok":
        raise RuntimeError(f"Backup integrity check failed: {result}")

    print(f"Backup created and verified: {backup_path.name}")
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention-days", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-delete", type=int, default=1000)
    parser.add_argument("--execute", action="store_true", help="Actually delete")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if args.retention_days < 1:
        print("--retention-days must be >= 1")
        return 1
    if not 1 <= args.batch_size <= 1000:
        print("--batch-size must be between 1 and 1000")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.retention_days)
    print(f"Cutoff: soft-deleted before {cutoff.isoformat()}")

    # 1. Đếm trước
    with SessionLocal() as session:
        total = count_candidates(session, cutoff)
    print(f"Conversations eligible for purge: {total}")

    if not args.execute:
        print("Dry-run only. Add --execute to delete.")
        return 0
    if total == 0:
        print("Nothing to purge.")
        return 0
    if total > args.max_delete:
        print(f"ABORT: {total} exceeds --max-delete={args.max_delete}")
        return 2

    # 2. Backup bắt buộc trước khi xóa
    try:
        backup_sqlite()
    except Exception as exc:  # noqa: BLE001
        print(f"ABORT: backup failed: {exc}")
        return 3

    # 3. Xác nhận
    if not args.yes:
        confirm = input(f"Type PURGE to permanently delete {total} conversations: ")
        if confirm != "PURGE":
            print("Aborted.")
            return 1

    # 4. Xóa theo lô, mỗi lô một transaction
    purged = 0
    while True:
        with SessionLocal() as session:
            try:
                ids = find_candidates(session, cutoff, args.batch_size)
                if not ids:
                    break
                session.execute(
                    delete(Conversation).where(
                        Conversation.id.in_(ids),
                        # Kiểm tra lại: tránh xóa chat vừa được khôi phục
                        Conversation.deleted_at.is_not(None),
                        Conversation.deleted_at <= cutoff,
                    )
                )
                session.commit()
                purged += len(ids)
                print(f"Purged batch: {len(ids)} (total {purged})")
            except Exception:
                session.rollback()
                print("Batch failed and was rolled back.")
                raise

    # 5. Kiểm tra sau khi xóa
    with SessionLocal() as session:
        orphans = count_orphans(session)

    print(f"Done. Purged {purged} conversations. Orphan messages: {orphans}")
    if orphans:
        print("WARNING: orphan messages found. Check that PRAGMA foreign_keys is ON.")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
