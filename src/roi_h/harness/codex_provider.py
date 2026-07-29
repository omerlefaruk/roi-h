"""ActiveGraph LLM provider backed by non-interactive Codex CLI."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from activegraph.llm import LLMBehaviorError, LLMMessage, LLMResponse
from pydantic import BaseModel, ConfigDict

_DEFAULT_MODEL = "codex-cli-default"
_NETWORK_ERROR = "llm.network_error"
_REQUEST_ERROR = "llm.request_error"
_SCHEMA_VIOLATION = "llm.schema_violation"


class _TextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class CodexCLIProvider:
    """Run one structured LLM completion through ``codex exec``.

    Codex runs ephemerally, read-only, without user or repository instructions.
    Saved Codex authentication is reused; no API key is handled by ROI-H.
    """

    def __init__(self, cwd: str | Path) -> None:
        """Bind Codex execution to one project working directory."""
        self._cwd = Path(cwd).expanduser().resolve()
        self._executable = os.environ.get("ROI_H_CODEX_BIN", "codex")
        configured_model = os.environ.get("ROI_H_CODEX_MODEL")
        self.default_model = configured_model or _DEFAULT_MODEL

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: type | None,
        timeout_seconds: float,
        tools: list[dict[str, Any]] | None = None,
        structured_output_mode: str = "prompt",
    ) -> LLMResponse:
        """Return the final typed Codex response as an ActiveGraph response."""
        del max_tokens, temperature, top_p, structured_output_mode
        if tools:
            msg = "CodexCLIProvider accepts typed decisions, not native tool calls"
            raise LLMBehaviorError(_REQUEST_ERROR, msg)

        response_type = cast("type[BaseModel]", output_schema or _TextOutput)
        schema = response_type.model_json_schema()
        prompt = _completion_prompt(system, messages)
        started = time.monotonic()
        try:
            raw_text = self._run_codex(
                prompt,
                schema=schema,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"codex exec exceeded {timeout_seconds:g}s"
            raise LLMBehaviorError(_NETWORK_ERROR, msg) from exc
        except FileNotFoundError as exc:
            msg = (
                f"Codex CLI executable not found: {self._executable!r}; "
                "install Codex or set ROI_H_CODEX_BIN"
            )
            raise LLMBehaviorError(_REQUEST_ERROR, msg) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "codex exec failed").strip()
            msg = detail[-2000:]
            raise LLMBehaviorError(
                _NETWORK_ERROR,
                msg,
                payload_extras={"returncode": exc.returncode},
            ) from exc

        try:
            payload = json.loads(raw_text)
            parsed = response_type.model_validate(payload)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            raise LLMBehaviorError(
                _SCHEMA_VIOLATION,
                msg,
                payload_extras={"raw_text": raw_text[-4000:]},
            ) from exc

        if output_schema is None:
            final_text = cast("_TextOutput", parsed).content
            parsed_output: Any = None
        else:
            final_text = raw_text
            parsed_output = parsed
        return LLMResponse(
            raw_text=final_text,
            parsed=parsed_output,
            input_tokens=_estimated_tokens(prompt),
            output_tokens=_estimated_tokens(raw_text),
            cost_usd=Decimal(0),
            latency_seconds=max(0.0, time.monotonic() - started),
            model=model,
            finish_reason="end_turn",
            provider_meta={"provider": "codex-cli"},
        )

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        """Codex CLI does not expose per-call price accounting."""
        del input_tokens, output_tokens, model
        return Decimal(0)

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        """Return a conservative local estimate for ActiveGraph gating."""
        del model
        return _estimated_tokens(_completion_prompt(system, messages))

    def supports_native_structured_output(self, model: str) -> bool:
        """Codex ``--output-schema`` enforces the declared output shape."""
        del model
        return True

    def recognizes_model(self, name: str) -> bool:
        """Codex CLI owns model-name validation."""
        return bool(name)

    def _run_codex(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        model: str,
        timeout_seconds: float,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="roi-h-codex-") as temp:
            temp_dir = Path(temp)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / "response.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                self._executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--cd",
                str(self._cwd),
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if model != _DEFAULT_MODEL:
                command.extend(["--model", model])
            command.append("-")
            completed = subprocess.run(  # noqa: S603
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=True,
            )
            if output_path.is_file():
                return output_path.read_text(encoding="utf-8").strip()
            return completed.stdout.strip()


def _completion_prompt(system: str, messages: list[LLMMessage]) -> str:
    conversation = [message.to_dict() for message in messages]
    return (
        "Act only as the reasoning engine for an ActiveGraph behavior. "
        "Do not run commands, edit files, browse, or call your own tools. "
        "Return only the JSON object required by the supplied output schema.\n\n"
        f"SYSTEM\n{system}\n\n"
        f"CONVERSATION\n{json.dumps(conversation, indent=2, sort_keys=True)}"
    )


def _estimated_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


__all__ = ["CodexCLIProvider"]
