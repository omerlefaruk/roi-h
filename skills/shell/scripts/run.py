from __future__ import annotations
import subprocess
from pydantic import BaseModel, Field
TOOL_ID="run"; DESCRIPTION="Run a shell command (approval required)."; DETERMINISTIC=False; REQUIRES_APPROVAL=True
class Input(BaseModel):
    command: str
    cwd: str = ""
    timeout_seconds: float = Field(default=120, ge=1, le=3600)
class Output(BaseModel):
    ok: bool = True
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
def run(args: Input) -> Output:
    completed = subprocess.run(
        args.command,
        shell=True,  # noqa: S602 — intentional gated shell tool
        cwd=args.cwd or None,
        capture_output=True,
        text=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    return Output(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout[:200_000],
        stderr=completed.stderr[:50_000],
    )
