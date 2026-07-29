"""Strict noninteractive command-line adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Never

from pydantic import ValidationError

from roi_h.agent.contract import CommandRequest, CommandResult
from roi_h.agent.dispatcher import Dispatcher, invalid_request_result

if TYPE_CHECKING:
    from collections.abc import Sequence


class AgentUsageError(ValueError):
    """Invalid agent command syntax."""


class _MachineParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise AgentUsageError(message)


def main(argv: Sequence[str]) -> int:
    """Run the agent adapter and emit one JSON result."""
    operation = "agent"
    request_id: str | None = None
    try:
        args = _parser().parse_args(list(argv))
        dispatcher = Dispatcher()
        if args.agent_command == "describe":
            result = dispatcher.describe(args.operation)
        elif args.agent_command == "context":
            operation = "system.context"
            request = CommandRequest(arguments=_context_arguments(args))
            result = dispatcher.execute(operation, request)
        else:
            operation = str(args.operation)
            request = _read_request(str(args.input))
            request = _add_secure_secret_input(args, operation, request)
            request_id = request.request_id
            result = dispatcher.execute(operation, request)
    except (AgentUsageError, json.JSONDecodeError, ValidationError, UnicodeError, OSError) as exc:
        result = invalid_request_result(operation, str(exc), request_id=request_id)
        _emit(result)
        return 2
    _emit(result)
    if result.ok:
        return 0
    if result.error and result.error.code in {"request.invalid", "operation.not_found"}:
        return 2
    return 1


def _parser() -> _MachineParser:
    parser = _MachineParser(prog="roi-h agent", add_help=False)
    sub = parser.add_subparsers(dest="agent_command", required=True)

    describe = sub.add_parser("describe", add_help=False)
    describe.add_argument("operation", nargs="?")

    context = sub.add_parser("context", add_help=False)
    context.add_argument("--home", default=None)
    context.add_argument("--project", default=None)
    context.add_argument("--env", choices=["dev", "prod"], default=None)

    call = sub.add_parser("call", add_help=False)
    call.add_argument("operation")
    call.add_argument("--input", required=True)
    call.add_argument("--secret-stdin", action="store_true")
    return parser


def _read_request(source: str) -> CommandRequest:
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source.removeprefix("@"))
        raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    return CommandRequest.model_validate(value)


def _add_secure_secret_input(
    args: argparse.Namespace,
    operation: str,
    request: CommandRequest,
) -> CommandRequest:
    if operation != "secret.set":
        if args.secret_stdin:
            message = "--secret-stdin is only valid for secret.set"
            raise AgentUsageError(message)
        return request
    if not args.secret_stdin:
        message = "secret.set requires --secret-stdin"
        raise AgentUsageError(message)
    if str(args.input) == "-":
        message = "secret.set requires a request file and separate secret stdin"
        raise AgentUsageError(message)
    if "secret_value" in request.arguments:
        message = "secret_value is not accepted in the request document"
        raise AgentUsageError(message)
    return request.model_copy(
        update={"arguments": {**request.arguments, "secret_value": sys.stdin.read()}}
    )


def _context_arguments(args: argparse.Namespace) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "home": args.home,
            "project": args.project,
            "environment": args.env,
        }.items()
        if value is not None
    }


def _emit(result: CommandResult) -> None:
    payload = (result.model_dump_json() + "\n").encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        return
    sys.stdout.write(payload.decode("utf-8"))


__all__ = ["main"]
