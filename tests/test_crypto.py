import pytest

from backend.crypto import CryptoError, CryptoModule


@pytest.fixture
def cm() -> CryptoModule:
    return CryptoModule()


def test_generate_keypair_sizes(cm: CryptoModule) -> None:
    public_key, private_key = cm.generate_keypair()
    assert len(public_key) == 1952
    assert len(private_key) == 4000


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
