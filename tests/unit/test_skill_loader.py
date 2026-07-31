"""Unit tests for skills discovery."""

import runpy
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from pypdf import PdfWriter

from roi_h.harness import loader
from roi_h.harness.loader import default_skills_root, load_skills
from roi_h.harness.skill_contract import (
    skill_tree_digest,
    strict_skill_model,
    strict_skill_schema,
)

_SHARED_TOOL = """\
from pydantic import BaseModel

TOOL_ID = "hello"
DESCRIPTION = "Shared hello"
TOOL_EFFECT = "read"

class Input(BaseModel):
    value: str = ""

class Output(BaseModel):
    ok: bool = True

def run(args: Input) -> Output:
    return Output()
"""


def test_load_skills_exposes_only_named_workflow_tools() -> None:
    catalog = load_skills(default_skills_root())
    names = {tool.name for tool in catalog.list_tools()}

    assert names == {
        "browser.click",
        "browser.download",
        "browser.fill",
        "browser.navigate",
        "browser.session_stop",
        "browser.snapshot",
        "codex_chrome.start",
        "codex_chrome.status",
        "codex_chrome.stop",
        "excel.read_rows",
        "excel.write_rows",
        "files.hash",
        "pdf.extract_text",
    }
    navigate = catalog.resolve("browser", "navigate")
    assert navigate.deterministic is False
    assert navigate.script_path.as_posix().endswith("skills/browser/scripts/navigate.py")
    assert navigate.network_hosts == ("*",)
    assert catalog.resolve("browser", "snapshot").allow_in_prod is False
    assert catalog.resolve("files", "hash").effect == "read"
    write_rows = catalog.resolve("excel", "write_rows")
    assert "sheet" in write_rows.input_schema["properties"]


def test_excel_skill_round_trips_the_named_summary_sheet(tmp_path: Path) -> None:
    scripts = default_skills_root() / "excel" / "scripts"
    writer = runpy.run_path(str(scripts / "write_rows.py"))
    reader = runpy.run_path(str(scripts / "read_rows.py"))
    path = tmp_path / "approved-invoice-summary.xlsx"
    rows = [{"report_id": "A", "approved_count": 2, "approved_total_usd": "200.00"}]
    headers = ["report_id", "approved_count", "approved_total_usd"]

    writer["run"](writer["Input"](path=str(path), sheet="summary", rows=rows, headers=headers))
    result = reader["run"](reader["Input"](path=str(path), sheet="summary"))

    assert result.headers == headers
    assert result.rows == [
        {"report_id": "A", "approved_count": "2", "approved_total_usd": "200.00"}
    ]


def test_pdf_skill_uses_the_packaged_extractor(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(path)
    extractor = runpy.run_path(str(default_skills_root() / "pdf" / "scripts" / "extract_text.py"))

    result = extractor["run"](extractor["Input"](path=str(path)))

    assert result.pages == 1
    assert result.text == ""


def test_strict_skill_models_reject_nested_coercion_and_extra_fields() -> None:
    class Nested(BaseModel):
        count: int

    class Input(BaseModel):
        nested: Nested

    strict = strict_skill_model(Input)

    with pytest.raises(ValidationError):
        strict.model_validate({"nested": {"count": "1", "unknown": True}})
    nested_schema = strict_skill_schema(Input)["$defs"]
    assert isinstance(nested_schema, dict)
    assert nested_schema["Nested"]["additionalProperties"] is False


def test_trusted_skill_import_keeps_its_tree_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "core"
    skill = root / "example"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# example\n", encoding="utf-8")
    (skill / "scripts" / "hello.py").write_text(_SHARED_TOOL, encoding="utf-8")
    before = skill_tree_digest(skill, reject_bytecode=False)
    monkeypatch.setattr(loader, "default_skills_root", lambda: root)

    catalog = loader.load_skills(root)

    assert catalog.resolve("example", "hello")
    assert skill_tree_digest(skill, reject_bytecode=False) == before
    assert not list(skill.rglob("*.pyc"))


def test_user_shared_skills_load_between_core_and_project(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared_skill = shared / "shared-example"
    (shared_skill / "scripts").mkdir(parents=True)
    (shared_skill / "SKILL.md").write_text("# shared-example\n", encoding="utf-8")
    (shared_skill / "scripts" / "hello.py").write_text(_SHARED_TOOL, encoding="utf-8")

    catalog = load_skills(default_skills_root(), shared_root=shared)
    tool = catalog.resolve("shared-example", "hello")

    assert tool.scope == "shared"
    assert catalog.shared_root == shared.resolve()
    assert tool.input_schema["properties"]["value"]["type"] == "string"
    assert tool.requires_approval is True
    assert tool.allow_in_prod is False


def test_custom_skill_inspection_ignores_exit_handler_output(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "exit-output"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# exit-output\n", encoding="utf-8")
    source = "import atexit\natexit.register(lambda: print('late output'))\n" + _SHARED_TOOL
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    assert load_skills(default_skills_root(), shared_root=shared).resolve("exit-output", "hello")


def test_custom_skill_inspection_blocks_import_effects(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "unsafe"
    marker = tmp_path / "import-effect"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# unsafe\n", encoding="utf-8")
    source = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n" + _SHARED_TOOL
    )
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot write files"):
        load_skills(default_skills_root(), shared_root=shared)

    assert not marker.exists()


def test_custom_skill_inspection_blocks_alternate_file_syscalls(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "unsafe-truncate"
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# unsafe-truncate\n", encoding="utf-8")
    source = f"import os\nos.truncate({str(victim)!r}, 0)\n" + _SHARED_TOOL
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"cannot perform os\.truncate"):
        load_skills(default_skills_root(), shared_root=shared)

    assert victim.read_text(encoding="utf-8") == "keep"


def test_custom_skill_inspection_blocks_parent_file_reads(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "unsafe-read"
    protected = tmp_path / "protected"
    protected.write_text("secret", encoding="utf-8")
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# unsafe-read\n", encoding="utf-8")
    source = f"from pathlib import Path\nPath({str(protected)!r}).read_text()\n" + _SHARED_TOOL
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot read files outside"):
        load_skills(default_skills_root(), shared_root=shared)


def test_custom_skill_inspection_blocks_process_replacement(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "unsafe-exec"
    marker = tmp_path / "exec-effect"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# unsafe-exec\n", encoding="utf-8")
    source = (
        "import os, sys\n"
        f"os.execv(sys.executable, [sys.executable, '-c', \"open({str(marker)!r}, 'w').close()\"])\n"
        + _SHARED_TOOL
    )
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"cannot perform os\.exec"):
        load_skills(default_skills_root(), shared_root=shared)

    assert not marker.exists()


def test_custom_skill_inspection_rejects_bytecode(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "bytecode"
    script = skill / "scripts" / "hello.py"
    (skill / "scripts" / "__pycache__").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# bytecode\n", encoding="utf-8")
    script.write_text(_SHARED_TOOL, encoding="utf-8")
    (skill / "scripts" / "__pycache__" / "hello.pyc").write_bytes(b"untrusted")

    with pytest.raises(ValueError, match="contains Python bytecode"):
        load_skills(default_skills_root(), shared_root=shared)


def test_custom_skill_inspection_rejects_native_extensions(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "native"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# native\n", encoding="utf-8")
    (skill / "scripts" / "hello.py").write_text(_SHARED_TOOL, encoding="utf-8")
    (skill / "scripts" / "native.so").write_bytes(b"untrusted")

    with pytest.raises(ValueError, match="contains a native Python extension"):
        load_skills(default_skills_root(), shared_root=shared)


def test_custom_skill_inspection_rejects_malformed_security_metadata(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    skill = shared / "malformed"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# malformed\n", encoding="utf-8")
    source = _SHARED_TOOL.replace(
        'TOOL_EFFECT = "read"',
        'TOOL_EFFECT = "read"\nALLOW_IN_PROD = "false"',
    )
    (skill / "scripts" / "hello.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="allow_in_prod"):
        load_skills(default_skills_root(), shared_root=shared)
