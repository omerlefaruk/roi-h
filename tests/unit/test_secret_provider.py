"""Secure secret-provider adapter tests."""

from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch
from uuid import uuid4

from roi_h.harness.secrets import MacOSKeychainSecretStore


def test_macos_keychain_write_does_not_put_value_in_process_arguments() -> None:
    secret = f"value-{uuid4().hex}"
    completed = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("roi_h.harness.secrets.subprocess.run", return_value=completed) as run:
        MacOSKeychainSecretStore().set("project", "dev", "TOKEN", secret)

    arguments = run.call_args.args[0]
    assert secret not in arguments
    assert arguments[-1] == "-w"
    assert run.call_args.kwargs["input"] == secret
