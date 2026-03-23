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
