from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"
ALLOWED_SKILLS = {"compare_bl_checklist"}

@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    instruction: str
    tool_name: str
    max_tool_rows_for_llm: int
    trigger_terms: tuple[str, ...]

@lru_cache(maxsize=16)
def load_skill(name: str) -> Skill:
    if name not in ALLOWED_SKILLS:
        raise ValueError(f"Skill is not allowed: {name}")
    folder = SKILLS_DIR / name
    config_path = folder / "config.json"
    instruction_path = folder / "SKILL.md"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("enabled", False):
        raise ValueError(f"Skill is disabled: {name}")
    instruction = instruction_path.read_text(encoding="utf-8").strip()
    if not instruction:
        raise ValueError(f"Skill instruction is empty: {name}")
    return Skill(
        name=config["name"],
        version=config["version"],
        instruction=instruction,
        tool_name=config["tool_name"],
        max_tool_rows_for_llm=int(config.get("max_tool_rows_for_llm", 200)),
        trigger_terms=tuple(str(x).casefold() for x in config.get("trigger_terms", [])),
    )

def should_trigger(skill: Skill, query: str) -> bool:
    normalized = " ".join((query or "").casefold().split())
    return any(term in normalized for term in skill.trigger_terms)
