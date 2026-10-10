import os

from fastapi import Depends, HTTPException

from shared.security import CurrentUser, get_current_user


def check_admin_id(user_id: str) -> None:
    if os.getenv("ADMIN_RESTORE_ENABLED", "").lower() != "true":
        raise HTTPException(
            status_code=403,
            detail="Admin restoration is disabled.",
        )

    admin_ids = {
        value.strip()
        for value in os.getenv("ADMIN_USER_IDS", "").split(",")
        if value.strip()
    }

    if not user_id or user_id not in admin_ids:
        raise HTTPException(
            status_code=403,
            detail="Admin permission required.",
        )


def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    check_admin_id(current_user.user_id)
    return current_user