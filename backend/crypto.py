"""CRYSTALS-Dilithium cryptography module.

This file handles all CRYSTALS-Dilithium cryptography via the liboqs library.
To change the algorithm used by the project, update this file only.
"""

import hashlib
import secrets

try:
    import oqs
    OQS_AVAILABLE = True
except ImportError:
    oqs = None
    OQS_AVAILABLE = False


DEFAULT_ALGORITHM = "ML-DSA-65"
VALID_ALGORITHMS = ["Dilithium2", "ML-DSA-65", "Dilithium5"]

# To replace Dilithium3 with another NIST post-quantum algorithm:
# 1) Change DEFAULT_ALGORITHM to the new mechanism name.
# 2) Update VALID_ALGORITHMS.
# 3) Update key-size numbers in method docstrings.
# 4) Run: python -c 'import oqs; print(oqs.get_enabled_sig_mechanisms())'
# No other file needs to change.


class CryptoError(Exception):
    """Raised when cryptography setup or usage is invalid."""


class CryptoModule:
    """Manage CRYSTALS-Dilithium cryptographic operations via liboqs.

    This class centralizes key generation, signing, verification, and identity
    derivation for CRYSTALS-Dilithium (NIST FIPS 204) using the liboqs Python
    bindings.
    """

    def __init__(self, algorithm: str = DEFAULT_ALGORITHM) -> None:
        """Initialize the crypto module with a supported Dilithium algorithm.

        Requirement: FR-00

        Args:
            algorithm (str): The Dilithium algorithm variant to use.

        Returns:
            None: This constructor does not return a value.

        Raises:
            CryptoError: If liboqs/oqs-python is unavailable or if the
                requested algorithm is not in the supported list.
        """
        self.algorithm = algorithm

        if OQS_AVAILABLE is False:
            raise CryptoError(
                "liboqs/oqs-python is not available. Install with: pip install oqs-python"
            )

        if self.algorithm not in VALID_ALGORITHMS:
            raise CryptoError(
                f"Invalid algorithm '{self.algorithm}'. Valid options: {VALID_ALGORITHMS}"
            )

    def generate_keypair(self) -> tuple[bytes, bytes]:
        """Generate a CRYSTALS-Dilithium public/private key pair.

        Requirement: FR-01

        Args:
            None: This method does not take external parameters.

        Returns:
            tuple[bytes, bytes]: A tuple of (public_key, private_key). For
                ML-DSA-65, the public key is 1952 bytes and the private key is
                4000 bytes.

        Raises:
            CryptoError: If key generation fails for any reason in liboqs.
        """
        try:
            with oqs.Signature(self.algorithm) as signer:
                public_key = signer.generate_keypair()
                private_key = signer.export_secret_key()
            return public_key, private_key
        except Exception as exc:
            raise CryptoError(f"Failed to generate keypair: {exc}") from exc

    def sign(self, private_key: bytes, message: bytes) -> bytes:
        """Sign a message using the configured CRYSTALS-Dilithium variant.

        Requirement: FR-02

        Args:
            private_key (bytes): The Dilithium private key used for signing.
            message (bytes): The message payload to sign.

        Returns:
            bytes: The generated signature. For ML-DSA-65, signature size is
                3293 bytes.

        Raises:
            CryptoError: If liboqs fails to sign the provided message.
        """
        try:
            with oqs.Signature(self.algorithm, secret_key=private_key) as signer:
                return signer.sign(message)
        except Exception as exc:
            raise CryptoError(f"Failed to sign message: {exc}") from exc

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Verify a CRYSTALS-Dilithium signature and return a boolean result.

        Requirement: FR-03

        Args:
            public_key (bytes): The public key to use for verification.
            message (bytes): The original message that was signed.
            signature (bytes): The signature bytes to validate.

        Returns:
            bool: True if the signature is valid for the message and key,
                otherwise False.

        Raises:
            CryptoError: Never raised by this method; failures are converted to
                False to handle low-level library errors safely.
        """
        if not all([public_key, message, signature]):
            return False

        try:
            with oqs.Signature(self.algorithm) as v:
                return v.verify(message, signature, public_key)
        except:
            return False

    def derive_node_id(self, public_key: bytes) -> str:
        """Derive a stable node identifier from a public key.

        Requirement: FR-04

        A node's identity is cryptographically bound to its key, so forging the
        identity requires forging the corresponding private key.

        Args:
            public_key (bytes): The node public key used as identity input.

        Returns:
            str: A 64-character lowercase hexadecimal SHA-256 digest.

        Raises:
            CryptoError: Never raised by this method under normal operation.
        """
        return hashlib.sha256(public_key).hexdigest()

    def generate_nonce(self) -> bytes:
        """Generate a cryptographically secure 256-bit nonce.

        Requirement: FR-05

        Uses the secrets module instead of random because secrets sources
        entropy from the operating system cryptographic random generator.

        Args:
            None: This method does not take external parameters.

        Returns:
            bytes: A 32-byte nonce suitable for cryptographic use.

        Raises:
            CryptoError: Never raised by this method under normal operation.
        """
        return secrets.token_bytes(32)
