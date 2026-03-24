import pytest
import os
import time
import builtins
import importlib.util
from pathlib import Path

from backend.crypto import CryptoError, CryptoModule
import backend.crypto as crypto_mod


@pytest.fixture
def cm() -> CryptoModule:
    return CryptoModule()


def test_generate_keypair_sizes(cm: CryptoModule) -> None:
    public_key, private_key = cm.generate_keypair()
    assert len(public_key) == 1952
    assert len(private_key) == 4032


def test_sign_and_verify_true(cm: CryptoModule) -> None:
    public_key, private_key = cm.generate_keypair()
    message = b"hello-dqrman"
    signature = cm.sign(private_key, message)
    assert cm.verify(public_key, message, signature) is True


def test_verify_false_when_message_tampered(cm: CryptoModule) -> None:
    public_key, private_key = cm.generate_keypair()
    message = bytearray(b"hello-dqrman")
    signature = cm.sign(private_key, bytes(message))
    message[0] ^= 0x01
    assert cm.verify(public_key, bytes(message), signature) is False


def test_verify_false_with_different_public_key(cm: CryptoModule) -> None:
    public_key_1, private_key_1 = cm.generate_keypair()
    public_key_2, _ = cm.generate_keypair()
    message = b"hello-dqrman"
    signature = cm.sign(private_key_1, message)
    assert cm.verify(public_key_2, message, signature) is False


def test_verify_returns_false_for_none_arguments(cm: CryptoModule) -> None:
    public_key, private_key = cm.generate_keypair()
    message = b"hello-dqrman"
    signature = cm.sign(private_key, message)

    assert cm.verify(None, message, signature) is False
    assert cm.verify(public_key, None, signature) is False
    assert cm.verify(public_key, message, None) is False


def test_generate_keypair_public_keys_are_different(cm: CryptoModule) -> None:
    public_key_1, _ = cm.generate_keypair()
    public_key_2, _ = cm.generate_keypair()
    assert public_key_1 != public_key_2


def test_derive_node_id_is_64_char_lower_hex(cm: CryptoModule) -> None:
    public_key, _ = cm.generate_keypair()
    node_id = cm.derive_node_id(public_key)
    assert len(node_id) == 64
    assert all(ch in "0123456789abcdef" for ch in node_id)


def test_generate_nonce_size(cm: CryptoModule) -> None:
    nonce = cm.generate_nonce()
    assert len(nonce) == 32


def test_invalid_algorithm_raises_crypto_error() -> None:
    with pytest.raises(CryptoError):
        CryptoModule("RSA2048")


def test_derive_node_id_is_deterministic(cm: CryptoModule) -> None:
    public_key, _ = cm.generate_keypair()
    node_id_1 = cm.derive_node_id(public_key)
    node_id_2 = cm.derive_node_id(public_key)
    assert node_id_1 == node_id_2


def test_sign_verify_latency() -> None:
    cm = CryptoModule()
    public_key, private_key = cm.generate_keypair()
    message = b"latency test"
    durations = []

    for _ in range(1000):
        start = time.perf_counter()
        signature = cm.sign(private_key, message)
        assert cm.verify(public_key, message, signature) is True
        durations.append(time.perf_counter() - start)

    durations.sort()
    p50 = durations[499]
    p95 = durations[949]
    p99 = durations[989]
    threshold_ms = float(os.getenv("AUTH_LATENCY_MS", "200"))

    print(f"p50={p50 * 1000:.3f}ms p95={p95 * 1000:.3f}ms p99={p99 * 1000:.3f}ms")
    assert durations[949] < (threshold_ms / 1000.0)


def test_crypto_module_import_handles_missing_oqs(monkeypatch: pytest.MonkeyPatch) -> None:
    module_path = Path(__file__).resolve().parents[1] / "backend" / "crypto.py"
    spec = importlib.util.spec_from_file_location("crypto_no_oqs", str(module_path))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "oqs":
            raise ImportError("oqs intentionally unavailable")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    spec.loader.exec_module(module)

    assert module.OQS_AVAILABLE is False
    assert module.oqs is None


def test_init_raises_when_oqs_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto_mod, "OQS_AVAILABLE", False)
    with pytest.raises(CryptoError):
        CryptoModule()


def test_generate_keypair_wraps_exceptions(cm: CryptoModule, monkeypatch: pytest.MonkeyPatch) -> None:
    class BadSignature:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(crypto_mod.oqs, "Signature", BadSignature)
    with pytest.raises(CryptoError):
        cm.generate_keypair()


def test_sign_wraps_exceptions(cm: CryptoModule, monkeypatch: pytest.MonkeyPatch) -> None:
    class BadSigner:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def sign(self, _message):
            raise RuntimeError("cannot sign")

    monkeypatch.setattr(crypto_mod.oqs, "Signature", BadSigner)
    with pytest.raises(CryptoError):
        cm.sign(b"k", b"m")


def test_verify_returns_false_on_verify_exception(cm: CryptoModule, monkeypatch: pytest.MonkeyPatch) -> None:
    class BadVerifier:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def verify(self, _message, _signature, _public_key):
            raise RuntimeError("cannot verify")

    monkeypatch.setattr(crypto_mod.oqs, "Signature", BadVerifier)
    assert cm.verify(b"pk", b"msg", b"sig") is False


if __name__ == "__main__":
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            __file__,
            "--cov=backend.crypto",
            "--cov-fail-under=80",
        ]
    )
    sys.exit(result.returncode)
