from __future__ import annotations

import re

import pytest

from lean_orchestrator.prompts import TEMPLATE_NAMES, PromptError, PromptLibrary, packaged_template


def test_every_packaged_template_exists_and_avoids_em_dashes():
    for name in TEMPLATE_NAMES:
        text = packaged_template(name)
        assert text.strip()
        assert "—" not in text, f"{name}.md contains an em dash"


def test_render_fills_common_and_rejects_unknown_placeholders(tmp_path):
    (tmp_path / "common.md").write_text("Role {{role}} in {{workspace}}; write {{output_path}} per {{schema_path}}.")
    (tmp_path / "custom.md").write_text("{{common}}\nTask {{task}}\n\n\n\nEnd")
    library = PromptLibrary(tmp_path)
    rendered = library.render("custom", role="critic", workspace="/w", output_path="o.json",
                              schema_path="s.json", task="check")
    assert rendered.startswith("Role critic in /w; write o.json per s.json.")
    assert "\n\n\n" not in rendered
    with pytest.raises(PromptError, match="task"):
        library.render("custom", role="r", workspace="w", output_path="o", schema_path="s")


def test_workspace_templates_override_packaged_ones(tmp_path):
    (tmp_path / "triage.md").write_text("custom triage {{problem_ids}}")
    library = PromptLibrary(tmp_path)
    assert library.render("triage", problem_ids="a, b") == "custom triage a, b\n"
    assert "SKEPTIC" in library.template("critic")


def test_placeholders_in_packaged_templates_are_documented():
    names = set()
    for template in TEMPLATE_NAMES:
        names |= set(re.findall(r"\{\{([a-z_]+)\}\}", packaged_template(template)))
    expected = {
        "common", "role", "workspace", "output_path", "schema_path", "problem_title", "problem_id", "problem_dir",
        "plan", "branch_guidance", "warnings", "lean_dir", "lean_module_prefix", "lean_project", "lean_libraries",
        "lean_library_note",
        "self_check", "formal_statement_note", "python_command", "python_packages", "report_path", "plan_path",
        "verification_path", "critic_dir", "branch_list", "iteration_materials", "assessment_instructions",
        "branch_notes", "escalation", "hints", "epoch_end_instructions", "relevance_block", "previous_literature",
        "qiqcop_mcp", "batch_path", "problem_ids", "failed_role", "errors", "previous_output",
    }
    assert names == expected
