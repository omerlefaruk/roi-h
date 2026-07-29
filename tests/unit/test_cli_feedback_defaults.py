from __future__ import annotations

from types import SimpleNamespace

import pytest

from roi_h import cli


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        ("dev", [], True),
        ("prod", [], False),
        ("dev", ["--no-feedback"], False),
        ("prod", ["--feedback"], True),
    ],
)
def test_run_feedback_policy_defaults_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    env: str,
    flag: list[str],
    expected: object,
) -> None:
    args = cli._build_parser().parse_args(  # noqa: SLF001
        ["rpa", "run", "example", "--env", env, *flag]
    )
    workspace = SimpleNamespace(env=env)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_workspace", lambda _args: workspace)

    def fake_run_automation(_workspace, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "run_automation", fake_run_automation)

    result = cli._cmd_run(args)  # noqa: SLF001

    assert result == {"ok": True}
    assert captured["collect_feedback"] is expected
