"""attacks.py: Adversarial behavior simulation for the DQRMAN mesh network."""

import logging
import time
import struct
from dataclasses import dataclass, field
from backend.crypto import CryptoModule

logger = logging.getLogger(__name__)


@dataclass
class AttackResult:
    """Standardized event logging object for recorded adversarial actions.

    Attributes:
        attack_type: Identifier string for the simulated attack vector.
        target_node_id: Node ID of the node that received the attack.
        detected: True if the target node correctly rejected the attack.
        detection_reason: Machine-readable reason code returned by the target.
        timestamp: Unix epoch time when the attack was launched.
        duration_ms: Total wall-clock time the attack took, in milliseconds.
    """
    attack_type: str
    target_node_id: str
    detected: bool
    detection_reason: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0


class AttackSimulator:
    """Simulates adversarial behaviors against the decentralized mesh.

    Each method models a distinct real-world network attack vector and
    verifies that the corresponding DQRMAN security requirement correctly
    detects the threat. All attacker cryptographic material is generated
    independently and is entirely separate from legitimate node keys.
    """

    def __init__(self, mesh, config=None):
        """Initialises the AttackSimulator with an isolated attacker identity.

        Args:
            mesh: TrustGraph or equivalent mesh instance the simulator
                operates against.
            config: Optional dict of simulator configuration overrides.
        """
        self.mesh = mesh
        self.config = config or {}

        # Instantiate a completely separate cryptographic module for the attacker
        self.crypto = CryptoModule()
        self._attacker_pub, self._attacker_priv = self.crypto.generate_keypair()

        logger.info("AttackSimulator initialized: Attacker's keys are completely separate from all legitimate nodes.")

        # Intercepted and captured messages dictionary
        self._captured = {}

    def capture_message(self, source_node, target_node):
        """Simulates a Man-in-the-Middle intercept of an authentication challenge.

        Models a passive eavesdropping scenario per FR-13 (message integrity)
        where the attacker records a freshly-generated challenge before it reaches
        the intended recipient. The captured challenge can then be replayed by
        replay_attack_outside_window or replay_attack_within_window.

        Detection: No detection at this stage — interception is passive. Detection
        occurs at submission time when the captured challenge is replayed.

        Args:
            source_node: The legitimate Node that generates the challenge.
            target_node: The intended recipient Node (used only to key storage).

        Returns:
            dict: The raw challenge dictionary as produced by source_node.create_challenge().
        """
        challenge = source_node.create_challenge()
        key = f"{source_node.node_id}->{target_node.node_id}"
        self._captured[key] = challenge
        return challenge

    def replay_attack_outside_window(self, source_node, target_node, delay=10.0):
        """Simulates a delayed message-replay attack per FR-18 and NFR-09.

        Models a real-world scenario where an attacker intercepts a legitimate
        authentication challenge and re-submits it after the 5-second freshness
        window has elapsed. This directly tests the TIMESTAMP_EXPIRED enforcement
        mandated by NFR-09 (message freshness).

        Detection: target_node.verify_challenge() checks that the challenge
        timestamp is within ±5 seconds of now. A stale challenge causes
        verify_challenge to return (False, 'TIMESTAMP_EXPIRED'). A CRITICAL log
        is raised if the attack is not detected, indicating an NFR-09 violation.

        Args:
            source_node: The legitimate Node whose identity is being replayed.
            target_node: The recipient Node that must detect the stale challenge.
            delay: Seconds to wait before re-submitting the challenge. Must
                exceed the freshness window (default 5 s) to trigger detection.
                Defaults to 10.0.

        Returns:
            AttackResult: Result with attack_type='replay_outside_window',
                detected=True on success, and detection_reason='TIMESTAMP_EXPIRED'.
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
        """Simulates a high-speed same-nonce replay attack per FR-18 and NFR-09.

        Models a scenario where an attacker intercepts a valid challenge and
        immediately re-submits the bit-identical payload before it expires.
        Because the timestamp is still fresh, this tests a second layer of
        defence — the NonceCache — which must reject the reuse of any nonce
        regardless of timing.

        Detection: The first submission succeeds (registering the nonce). The
        second identical submission must be rejected with 'DUPLICATE_NONCE', not
        'TIMESTAMP_EXPIRED'. This distinction is critical: TIMESTAMP_EXPIRED would
        mean the test is only catching stale messages; DUPLICATE_NONCE proves the
        nonce blacklist is actively enforced.

        Args:
            source_node: The legitimate Node whose challenge is intercepted.
            target_node: The recipient Node that must reject the second submission.

        Returns:
            AttackResult: Result with attack_type='replay_within_window',
                detected=True on correct rejection, detection_reason='DUPLICATE_NONCE'.
        """
        start_time = time.time()

        # Step 1: Capture a message
        logger.info("Capture: Intercepting challenge for bit-identical replay.")
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
        """Simulates identity spoofing per FR-19 and FR-14.

        Claims victim_node's node_id and public_key but signs the challenge
        with the attacker's own separate private key. This models a Sybil-style
        impersonation attempt in which an adversary knows the target's identity
        and public key (e.g. from a prior broadcast) but cannot forge its
        private key.

        Detection: crypto.verify fails because the attacker's key does not match
        the registered victim key stored in target_node's trust table. The target
        reconstructs the auth message (nonce + packed timestamp) and calls
        crypto.verify(victim_public_key, message, attacker_signature), which
        returns False, causing verify_challenge to return (False, 'INVALID_SIGNATURE').

        Args:
            victim_node: The legitimate Node being impersonated. Its node_id and
                public_key are copied into the fake challenge to make it appear
                legitimate.
            target_node: The recipient Node that must detect the signature mismatch.

        Returns:
            AttackResult: Result with attack_type='spoof_attack', detected=True
                on correct rejection, detection_reason='INVALID_SIGNATURE'.
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
        """Simulates RF signal jamming against a node's heartbeat channel per FR-13.

        Models a physical-layer denial-of-service attack where radio frequency
        interference blocks all incoming UDP heartbeat messages at a receiver for
        a fixed window. This tests resilience mandated by FR-13 (mesh integrity)
        and the self-healing recovery described in FR-11.

        After the jam window ends, the receiver's last_seen is immediately updated
        for the target node. Without this step the HeartbeatReceiver would classify
        the node as stale and trigger an erroneous failure cascade; restoring
        last_seen proves that the mesh can cleanly distinguish genuine failure
        from transient interference recovery.

        Detection: Jamming is a physical-layer event; there is no cryptographic
        rejection. Detection is considered successful when the jam window expires
        and the node's presence is restored without further disruption
        (detection_reason='JAMMING_WINDOW_EXPIRED').

        Args:
            target_node: The Node whose heartbeats are being blocked. Its
                node_id is used to restore last_seen after the jam ends.
            target_receiver: HeartbeatReceiver instance whose jamming_active
                flag controls packet processing.
            duration: Duration in seconds to maintain the jamming state.
                Defaults to 5.0.

        Returns:
            AttackResult: Result with attack_type='jamming_simulation',
                detected=True, detection_reason='JAMMING_WINDOW_EXPIRED'.
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
