from __future__ import annotations

import gzip
import importlib.util
import io
import ssl
import sys
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_http_get():
    path = REPO_ROOT / "skills/http/scripts/get.py"
    spec = importlib.util.spec_from_file_location("http_get_error_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_http_get_returns_remote_http_response(monkeypatch) -> None:
    module = _load_http_get()
    error = urllib.error.HTTPError(
        "https://example.test",
        403,
        "Forbidden",
        {
            "Content-Encoding": "gzip",
            "Content-Type": "application/json; charset=utf-8",
        },
        io.BytesIO(gzip.compress(b'{"blocked": true}')),
    )
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _raise(error))

    result = module.run(module.Input(url="https://example.test"))

    assert result.ok is False
    assert result.status == 403
    assert result.json_data == {"blocked": True}
    assert result.error.category == "remote_http"
    assert result.error.code == "http.remote_response"
    assert result.error.retryable is False


def test_http_get_returns_remote_tls_error(monkeypatch) -> None:
    module = _load_http_get()
    error = urllib.error.URLError(ssl.SSLError(1, "TLS handshake failed"))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _raise(error))

    result = module.run(module.Input(url="https://example.test"))

    assert result.ok is False
    assert result.status == 0
    assert result.error.category == "remote_tls"
    assert result.error.code == "http.remote_tls_error"
    assert result.error.retryable is True


def test_http_get_returns_remote_network_error(monkeypatch) -> None:
    module = _load_http_get()
    error = urllib.error.URLError(OSError("network unavailable"))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _raise(error))

    result = module.run(module.Input(url="https://example.test"))

    assert result.ok is False
    assert result.status == 0
    assert result.error.category == "remote_network"
    assert result.error.code == "http.remote_network_error"
    assert result.error.retryable is True


def _raise(error: Exception):
    raise error
