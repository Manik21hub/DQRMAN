"""CRYSTALS-Dilithium cryptography module.

This file handles all CRYSTALS-Dilithium cryptography via the liboqs library.
To change the algorithm used by the project, update this file only.
"""

try:
    import oqs
    OQS_AVAILABLE = True
except ImportError:
    oqs = None
    OQS_AVAILABLE = False


DEFAULT_ALGORITHM = "Dilithium3"
VALID_ALGORITHMS = ["Dilithium2", "Dilithium3", "Dilithium5"]


class CryptoError(Exception):
    """Raised when cryptography setup or usage is invalid."""


class CryptoModule:
    """Provides validation and setup for Dilithium operations."""

    def __init__(self, algorithm: str = DEFAULT_ALGORITHM) -> None:
        if not OQS_AVAILABLE:
            raise CryptoError("liboqs/oqs-python is not available in this environment")

        if algorithm not in VALID_ALGORITHMS:
            raise CryptoError(
                f"Invalid algorithm '{algorithm}'. Valid options: {VALID_ALGORITHMS}"
            )

        self.algorithm = algorithm

    def generate_keypair(self) -> tuple[bytes, bytes]:
        """Generate a Dilithium keypair; for Dilithium3, public key is 1952 bytes and private key is 4000 bytes."""
        try:
            with oqs.Signature(self.algorithm) as signer:
                public_key = signer.generate_keypair()
                private_key = signer.export_secret_key()
            return public_key, private_key
        except Exception as exc:
            raise CryptoError(f"Failed to generate keypair: {exc}") from exc

    def sign(self, private_key: bytes, message: bytes) -> bytes:
        """Sign a message using Dilithium3; for Dilithium3, signature size is 3293 bytes."""
        try:
            with oqs.Signature(self.algorithm, secret_key=private_key) as signer:
                return signer.sign(message)
        except Exception as exc:
            raise CryptoError(f"Failed to sign message: {exc}") from exc

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        if not all([public_key, message, signature]):
            return False

        try:
            with oqs.Signature(self.algorithm) as v:
                return v.verify(message, signature, public_key)
        except:
            return False
