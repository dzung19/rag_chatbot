import logging
import uuid
from datetime import datetime
from typing import Optional, List

from shared.models import Skill, SkillCreateRequest, SkillUpdateRequest, SkillType
from shared.sqlite_db import get_sqlite_connection

logger = logging.getLogger(__name__)

class SkillNotFoundError(Exception):
    pass

class SkillSystemError(Exception):
    pass

def _row_to_skill(row: dict) -> Skill:
    # Convert enabled_tools from string to list (if needed in phase 2)
    # For now, it's just '[]'
    import json
    enabled_tools = json.loads(row["enabled_tools"]) if row["enabled_tools"] else []
    
    return Skill(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        type=row["type"],
        category=row["category"],
        is_system=bool(row["is_system"]),
        system_prompt_addon=row["system_prompt_addon"],
        temperature_override=row["temperature_override"],
        top_k_override=row["top_k_override"],
        icon=row["icon"],
        enabled_tools=enabled_tools,
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )

def get_skill(skill_id: str) -> Optional[Skill]:
    """Retrieve a skill by its ID."""
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        return None
    return _row_to_skill(dict(row))

def list_skills(skill_type: Optional[SkillType] = None) -> List[Skill]:
    """List all skills, optionally filtered by type."""
    conn = get_sqlite_connection()
    cur = conn.cursor()
    
    if skill_type:
        cur.execute("SELECT * FROM skills WHERE type = ? ORDER BY category ASC, name ASC", (skill_type.value,))
    else:
        cur.execute("SELECT * FROM skills ORDER BY category ASC, name ASC")
        
    rows = cur.fetchall()
    return [_row_to_skill(dict(r)) for r in rows]

def create_skill(request: SkillCreateRequest) -> Skill:
    """Create a new custom skill."""
    from shared.security import detect_prompt_injection
    
    # Basic protection against obvious injections
    if detect_prompt_injection(request.system_prompt_addon):
        raise ValueError("Prompt injection detected in system_prompt_addon.")
        
    conn = get_sqlite_connection()
    
    skill_id = request.id or str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    try:
        conn.execute(
            """
            INSERT INTO skills (
                id, name, description, type, category, is_system, 
                system_prompt_addon, temperature_override, top_k_override, 
                icon, enabled_tools, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'custom', 0, ?, ?, ?, ?, '[]', ?, ?)
            """,
            (
                skill_id, request.name, request.description, request.type.value,
                request.system_prompt_addon, request.temperature_override, request.top_k_override,
                request.icon, now, now
            )
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to create skill {skill_id}: {e}")
        raise
        
    return get_skill(skill_id)

def update_skill(skill_id: str, request: SkillUpdateRequest) -> Skill:
    """Update an existing custom skill."""
    conn = get_sqlite_connection()
    
    # Verify it exists and is not system
    existing = get_skill(skill_id)
    if not existing:
        raise SkillNotFoundError(f"Skill {skill_id} not found.")
    if existing.is_system:
        raise SkillSystemError("Built-in base skills cannot be modified.")
        
    from shared.security import detect_prompt_injection
    if request.system_prompt_addon and detect_prompt_injection(request.system_prompt_addon):
        raise ValueError("Prompt injection detected in system_prompt_addon.")

    # Build dynamic update
    updates = []
    params = []
    for field, value in request.model_dump(exclude_unset=True).items():
        updates.append(f"{field} = ?")
        params.append(value)
        
    if not updates:
        return existing
        
    now = datetime.utcnow().isoformat()
    updates.append("updated_at = ?")
    params.append(now)
    params.append(skill_id)
    
    query = f"UPDATE skills SET {', '.join(updates)} WHERE id = ?"
    
    conn.execute(query, tuple(params))
    conn.commit()
    
    return get_skill(skill_id)

def delete_skill(skill_id: str) -> bool:
    """Delete a custom skill."""
    conn = get_sqlite_connection()
    
    existing = get_skill(skill_id)
    if not existing:
        return False
    if existing.is_system:
        raise SkillSystemError("Built-in base skills cannot be deleted.")
        
    conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    conn.commit()
    return True
