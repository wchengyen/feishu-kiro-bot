#!/usr/bin/env python3
"""Scanner for Kiro CLI agents and skills."""

import json
import os
from pathlib import Path

import yaml

AGENTS_DIR = Path(os.path.expanduser("~/.kiro/agents"))
SKILLS_DIR = Path(os.path.expanduser("~/.kiro/skills"))


def list_agents() -> list[dict]:
    """Scan ~/.kiro/agents/*.json and return list of agent info dicts."""
    agents_dir = Path(AGENTS_DIR)
    if not agents_dir.exists():
        return []

    agents: list[dict] = []
    for agent_file in agents_dir.glob("*.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        agents.append(
            {
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "tools": data.get("tools", []),
                "resources": data.get("resources", []),
            }
        )

    return agents


def _extract_frontmatter(content: str) -> tuple[str | None, str]:
    """Extract YAML frontmatter from markdown content.

    Returns (frontmatter_yaml, remaining_content) or (None, content) if no frontmatter.
    """
    if not content.startswith("---"):
        return None, content

    # Find the closing ---
    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        return None, content

    frontmatter = content[3:end_idx].strip()
    remaining = content[end_idx + 4 :].lstrip("\n")
    return frontmatter, remaining


def list_skills() -> list[dict]:
    """Scan ~/.kiro/skills/**/SKILL.md and return list of skill info dicts."""
    skills_dir = Path(SKILLS_DIR)
    if not skills_dir.exists():
        return []

    skills: list[dict] = []
    for skill_file in skills_dir.rglob("SKILL.md"):
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue

        frontmatter, remainder = _extract_frontmatter(content)

        if frontmatter is not None:
            try:
                meta = yaml.safe_load(frontmatter) or {}
            except yaml.YAMLError:
                meta = {}
        else:
            meta = {}

        if "name" in meta:
            name = meta["name"]
        else:
            name = skill_file.parent.name

        if "description" in meta:
            description = meta["description"]
        else:
            # Use first non-empty line as description
            first_line = content.strip().splitlines()[0] if content.strip() else ""
            description = first_line

        skills.append(
            {
                "name": name,
                "description": description,
                "triggers": meta.get("triggers", []),
                "path": str(skill_file),
            }
        )

    return skills
