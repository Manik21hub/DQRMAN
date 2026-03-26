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
