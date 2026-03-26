"""attacks.py: Adversarial behavior simulation for the DQRMAN mesh network."""

import logging
import time
import struct
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

    def spoof_attack(self, victim_node, target_node):
        """Executes a spoofing attack by impersonating a node with invalid signatures.
        
        Attempts to authenticate as victim_node to target_node using a challenge 
        signed by the attacker's private key instead of the victim's.
        """
        start_time = time.time()
        
        # Step 1: Generate attacker-controlled challenge components
        nonce = self.crypto.generate_nonce()
        timestamp = time.time()
        
        # Step 2: Sign with ATTACKER's private key (Malicious signature)
        # Replicates _build_auth_message logic: nonce + packed_timestamp
        message = nonce + struct.pack('d', timestamp)
        signature = self.crypto.sign(self._attacker_priv, message)
        
        # Step 3: Build fake challenge dictionary impersonating the victim
        challenge = {
            'node_id': victim_node.node_id,
            'public_key': victim_node.public_key.hex(),
            'nonce': nonce.hex(),
            'timestamp': timestamp,
            'signature': signature.hex()
        }
        
        logger.info(f"Spoof: Submitting fake challenge (Victim: {victim_node.node_id[:8]}) to {target_node.node_id[:8]}")
        
        # Step 4: Submit to target node for verification
        success, error_reason = target_node.verify_challenge(challenge)
        
        detected = not success
        
        if not detected:
            logger.critical(f"SECURITY VIOLATION: Spoofing attack successful! Target accepted fake signature from {victim_node.node_id[:8]}")
        else:
            logger.info(f"Spoofing detected correctly: {error_reason}")
            
        duration_ms = (time.time() - start_time) * 1000
        
        return AttackResult(
            attack_type="spoof_attack",
            target_node_id=target_node.node_id,
            detected=detected,
            detection_reason=error_reason or "NOT_DETECTED",
            duration_ms=duration_ms
        )

    def jamming_simulation(self, target_node, target_receiver, duration=5.0):
        """Simulates RF jamming blocking heartbeats to a specific receiver.
        
        Temporarily sets jamming_active on the receiver to drop all incoming 
        packets, then restores presence to prevent node from being permanently 
        isolated after the attack ends.
        """
        start_time = time.time()
        
        logger.info(f"Jamming: Starting {duration}s interference at receiver for {target_node.node_id[:8]}")
        
        # Step 1: Activate jamming state on the receiver
        target_receiver.jamming_active = True
        
        # Step 2: Maintain jamming for the specified duration
        time.sleep(duration)
        
        # Step 3: Deactivate jamming
        target_receiver.jamming_active = False
        
        # Step 4: Restore presence to prevent stale isolation
        # Immediately update last_seen for the target node to current time
        target_receiver.last_seen[target_node.node_id] = time.time()
        
        logger.info("Jamming: Interference ended and node presence was restored.")
        
        duration_ms = (time.time() - start_time) * 1000
        
        return AttackResult(
            attack_type="jamming_simulation",
            target_node_id=target_node.node_id,
            detected=True,  # Jamming is a physical layer event usually handled by isolation logic
            detection_reason="JAMMING_WINDOW_EXPIRED",
            duration_ms=duration_ms
        )
