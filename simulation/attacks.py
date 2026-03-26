"""attacks.py: Adversarial behavior simulation for the DQRMAN mesh network."""

import logging
import time
from dataclasses import dataclass, field
from backend.crypto import CryptoModule

logger = logging.getLogger(__name__)


@dataclass
class AttackResult:
    """Standardized event logging object for recorded adversarial actions."""
    attack_type: str
    target_node_id: str
    detected: bool
    detection_reason: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0


class AttackSimulator:
    """Simulates adversarial behaviors against the decentralized mesh."""

    def __init__(self, mesh, config=None):
        self.mesh = mesh
        self.config = config or {}
        
        # Instantiate a completely separate cryptographic module for the attacker
        self.crypto = CryptoModule()
        self._attacker_pub, self._attacker_priv = self.crypto.generate_keypair()
        
        logger.info("AttackSimulator initialized: Attacker's keys are completely separate from all legitimate nodes.")
        
        # Intercepted and captured messages dictionary
        self._captured = {}

    def capture_message(self, source_node, target_node):
        """Captures a fresh challenge from a source node for potential replay or analysis."""
        challenge = source_node.create_challenge()
        key = f"{source_node.node_id}->{target_node.node_id}"
        self._captured[key] = challenge
        return challenge

    def replay_attack_outside_window(self, source_node, target_node, delay=10.0):
        """Executes a replay attack by submitting a captured message after the time window.
        
        Tests NFR-09 requirement: Replay attacks must be detected by the 5-second 
        freshness window.
        """
        start_time = time.time()
        
        # Step 1: Capture a message
        logger.info(f"Capture: Intercepting challenge from {source_node.node_id[:8]} -> {target_node.node_id[:8]}")
        challenge = self.capture_message(source_node, target_node)
        
        # Step 2: Sleep for the delay to exceed the default 5s window
        logger.info(f"Replay: Waiting {delay}s to exceed freshness window...")
        time.sleep(delay)
        
        # Step 3: Submit to target node for verification
        success, error_reason = target_node.verify_challenge(challenge)
        
        # Detection occurs if verify_challenge fails
        detected = not success
        detection_reason = error_reason if not detected else "REPLAY_DETECTION_WORKING"
        
        if not detected:
            logger.critical("NFR-09 VIOLATION: Replay attack outside the time window was successful!")
        else:
            logger.info(f"Replay detected successfully: {error_reason}")
            
        duration_ms = (time.time() - start_time) * 1000
        
        return AttackResult(
            attack_type="replay_outside_window",
            target_node_id=target_node.node_id,
            detected=detected,
            detection_reason=error_reason or "NOT_DETECTED",
            duration_ms=duration_ms
        )

    def replay_attack_within_window(self, source_node, target_node):
        """Executes an immediate replay attack to verify nonce blacklisting.
        
        Schedules two identical submissions. The first should pass (or fail 
        legitimately), the second must be rejected as a duplicate nonce.
        """
        start_time = time.time()
        
        # Step 1: Capture a message
        logger.info(f"Capture: Intercepting challenge for bit-identical replay.")
        challenge = self.capture_message(source_node, target_node)
        
        # Step 2: Submit first time to register the nonce in target's cache
        logger.info("Replay: Submitting first attempt (Registration)...")
        target_node.verify_challenge(challenge)
        
        # Step 3: Immediately submit the exact same challenge again
        logger.info("Replay: Submitting second attempt (Immediate Replay)...")
        success, error_reason = target_node.verify_challenge(challenge)
        
        detected = not success
        
        # Validation: Must be rejected because of DUPLICATE_NONCE, not timing
        if detected:
            assert error_reason != 'TIMESTAMP_EXPIRED', "Replay detected as expired instead of duplicate!"
            assert error_reason == 'DUPLICATE_NONCE', f"Expected DUPLICATE_NONCE but got {error_reason}"
            logger.info(f"Nonce replay correctly detected: {error_reason}")
        else:
            logger.critical("NFR-09 VIOLATION: Immediate nonce replay was accepted by the target node!")
            
        duration_ms = (time.time() - start_time) * 1000
        
        return AttackResult(
            attack_type="replay_within_window",
            target_node_id=target_node.node_id,
            detected=detected,
            detection_reason=error_reason or "NOT_DETECTED",
            duration_ms=duration_ms
        )
