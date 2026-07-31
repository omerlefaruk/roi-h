"""Generic-core publication guard."""

from scripts.check_publication_boundary import publication_violations


def test_rejects_private_runtime_and_customer_material() -> None:
    paths = [
        ".roi-h/projects/acme/secrets.json",
        "analysis/customer-session.md",
        "challenge.xlsx",
        "skills/ata/SKILL.md",
        "skills/feedback/scripts/record.py",
        "skills/http/scripts/get.py",
        "skills/shell/scripts/run.py",
        "src/roi_h/_agent_skills/customer-private/SKILL.md",
        "projects/acme/dev/automations/job/recipe.json",
        "customer/browser-profile/Default/Cookies",
    ]

    assert publication_violations(paths) == sorted(paths)


def test_accepts_retired_core_skills_only_in_history() -> None:
    paths = [
        "skills/feedback/scripts/record.py",
        "skills/http/scripts/get.py",
        "skills/shell/scripts/run.py",
    ]

    assert publication_violations(paths) == paths
    assert publication_violations(paths, history=True) == []
    assert publication_violations(["skills/http/customer/acme.txt"], history=True) == [
        "skills/http/customer/acme.txt"
    ]


def test_accepts_generic_core_files_and_public_skills() -> None:
    paths = [
        "README.md",
        "docs/distribution-and-updates.md",
        "skills/browser/SKILL.md",
        "skills/codex_chrome/SKILL.md",
        "skills/excel/scripts/read_rows.py",
        "src/roi_h/_agent_skills/migrate-code-automation/SKILL.md",
        "src/roi_h/_agent_skills/migrate-code-automation/agents/openai.yaml",
        "src/roi_h/harness/automation.py",
        "tests/unit/test_workspace_projects.py",
    ]

    assert publication_violations(paths) == []
