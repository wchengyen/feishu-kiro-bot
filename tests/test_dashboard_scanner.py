#!/usr/bin/env python3
"""Tests for dashboard.kiro_scanner"""

import json
import pytest

from dashboard.kiro_scanner import list_agents, list_skills


class TestListAgents:
    def test_list_agents_returns_list_with_name_and_description(self, tmp_path, monkeypatch):
        """list_agents should return a list of dicts with name and description."""
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)

        # Create a valid agent JSON file
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "TestAgent",
            "description": "A test agent",
            "tools": ["tool1", "tool2"],
            "resources": ["res1"],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))
        agents = list_agents()

        assert isinstance(agents, list)
        assert len(agents) == 1
        assert agents[0]["name"] == "TestAgent"
        assert agents[0]["description"] == "A test agent"
        assert agents[0]["tools"] == ["tool1", "tool2"]
        assert agents[0]["resources"] == ["res1"]

    def test_list_agents_skips_malformed_json(self, tmp_path, monkeypatch):
        """list_agents should skip files that fail to parse."""
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)

        valid = agents_dir / "valid.json"
        valid.write_text(json.dumps({"name": "Valid", "description": "ok"}))

        invalid = agents_dir / "invalid.json"
        invalid.write_text("not json")

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))
        agents = list_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "Valid"

    def test_list_agents_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        """list_agents should return empty list when agents dir doesn't exist."""
        nonexistent = tmp_path / ".kiro" / "agents"
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(nonexistent))
        agents = list_agents()
        assert agents == []


class TestListSkills:
    def test_list_skills_returns_list_with_name_and_description(self, tmp_path, monkeypatch):
        """list_skills should return a list of dicts with name and description."""
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir(parents=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: MySkill
description: A test skill
triggers: ["trigger1", "trigger2"]
---

# Content
""")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        skills = list_skills()

        assert isinstance(skills, list)
        assert len(skills) == 1
        assert skills[0]["name"] == "MySkill"
        assert skills[0]["description"] == "A test skill"
        assert skills[0]["triggers"] == ["trigger1", "trigger2"]
        assert "path" in skills[0]

    def test_list_skills_missing_frontmatter_uses_fallback(self, tmp_path, monkeypatch):
        """If frontmatter is missing, use directory name as name, first line as description."""
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "fallback-skill"
        skill_dir.mkdir(parents=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("This is the first line.\n\nMore content.\n")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        skills = list_skills()

        assert len(skills) == 1
        assert skills[0]["name"] == "fallback-skill"
        assert skills[0]["description"] == "This is the first line."

    def test_list_skills_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        """list_skills should return empty list when skills dir doesn't exist."""
        nonexistent = tmp_path / ".kiro" / "skills"
        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(nonexistent))
        skills = list_skills()
        assert skills == []
