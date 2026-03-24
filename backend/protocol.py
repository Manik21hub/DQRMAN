"""protocol.py: Wire protocol for DQRMAN mesh network communication.

This module implements the message framing protocol, including serialization,
deserialization, and TCP client/server for peer-to-peer mesh communication.
"""

import struct
import socket
import threading
import logging
from typing import Dict, Any

# Module-level logger
logger = logging.getLogger(__name__)


# Message type constants
MESSAGE_TYPES = {
    0x01: 'CHALLENGE',
    0x02: 'RESPONSE',
    0x03: 'HEARTBEAT',
    0x04: 'MESH_UPDATE',
    0x05: 'JOIN',
    0x06: 'JOIN_ACK',
}


def pack_frame(message_type, sender_node_id, payload, signature):
    """Pack a message frame with length prefix and type information.

    Constructs a wire frame by combining message type, sender node ID,
    payload, and signature. Prepends a 4-byte big-endian length prefix
    for stream-based transmission.

    Args:
        message_type: Integer message type code (0x01 - 0x06).
        sender_node_id: 64-character hex string node identifier.
        payload: Bytes containing the message payload.
        signature: Bytes containing the Dilithium signature.

    Returns:
        bytes: Complete frame with 4-byte length prefix followed by body.

    Raises:
        None.
    """
    # Convert node_id from 64-char hex to 32 bytes
    node_id_bytes = bytes.fromhex(sender_node_id)

    # Build body: type byte + 32 node_id bytes + payload + signature
    body = struct.pack('B', message_type) + node_id_bytes + payload + signature

    # Prepend 4-byte big-endian length
    length = len(body)
    frame = struct.pack('>I', length) + body

    return frame


def unpack_frame(frame_bytes):
    """Unpack a message frame and extract components.

    Parses a frame created by pack_frame, extracting the message type,
    sender node ID, and remaining body bytes. Includes human-readable
    message type name from MESSAGE_TYPES dictionary.

    Args:
        frame_bytes: Complete frame bytes including 4-byte length prefix.

    Returns:
        dict: Dictionary with keys:
            - message_type: Integer message type code
            - type_name: String name from MESSAGE_TYPES
            - sender_node_id: 64-character hex string
            - body: Remaining bytes (payload + signature)

    Raises:
        None.
    """
    if len(frame_bytes) < 5:  # 4-byte length + at least 1 byte body
        return {}

    # Extract and validate length
    length = struct.unpack('>I', frame_bytes[:4])[0]
    if len(frame_bytes) < 4 + length:
        return {}

    # Extract body
    body = frame_bytes[4:4 + length]

    if len(body) < 33:  # 1 byte type + 32 bytes node_id
        return {}

    # Extract message_type and node_id
    message_type = struct.unpack('B', body[0:1])[0]
    node_id_bytes = body[1:33]
    node_id_hex = node_id_bytes.hex()

    # Get type name
    type_name = MESSAGE_TYPES.get(message_type, 'UNKNOWN')

    # Remaining body bytes
    remaining_body = body[33:]

    return {
        'message_type': message_type,
        'type_name': type_name,
        'sender_node_id': node_id_hex,
        'body': remaining_body,
    }


class NodeTCPServer:
    """TCP server for receiving mesh network frames.

    Listens on a specified port for incoming frames, parses them using
    the wire protocol, and invokes handler callbacks. Supports concurrent
    connections via threading.
    """

    def __init__(self, host='127.0.0.1', port_start=10000):
        """Initialize TCP server.

        Args:
            host: Host address to bind to (default '127.0.0.1').
            port_start: Starting port number (default 10000).

        Returns:
            None.

        Raises:
            None.
        """
        self.host = host
        self.port = port_start
        self.socket = None
        self.running = False
        self.thread = None
        self.handler = None

    def start(self, handler=None):
        """Start the TCP server in background thread.

        Args:
            handler: Callable that receives unpacked frame dict.

        Returns:
            None.

        Raises:
            None.
        """
        self.handler = handler
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            logger.info(f'TCP server listening on {self.host}:{self.port}')
        except OSError as e:
            logger.error(f'Failed to bind socket: {e}')
            return

        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def _accept_loop(self):
        """Accept incoming connections and handle frames."""
        while self.running:
            try:
                client_socket, addr = self.socket.accept()
                thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, addr),
                    daemon=True
                )
                thread.start()
            except Exception as e:
                if self.running:
                    logger.error(f'Accept error: {e}')
                break

    def _handle_client(self, client_socket, addr):
        """Handle a single client connection."""
        try:
            while self.running:
                # Read 4-byte length prefix
                length_bytes = client_socket.recv(4)
                if not length_bytes:
                    break

                length = struct.unpack('>I', length_bytes)[0]

                # Read frame body
                body = b''
                remaining = length
                while remaining > 0:
                    chunk = client_socket.recv(min(remaining, 4096))
                    if not chunk:
                        break
                    body += chunk
                    remaining -= len(chunk)

                # Unpack and invoke handler
                frame = length_bytes + body
                unpacked = unpack_frame(frame)
                if unpacked and self.handler:
                    self.handler(unpacked)

        except Exception as e:
            logger.error(f'Client {addr} error: {e}')
        finally:
            client_socket.close()

    def stop(self):
        """Stop the TCP server.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.running = False
        if self.socket:
            self.socket.close()
        if self.thread:
            self.thread.join(timeout=2.0)


class NodeTCPClient:
    """TCP client for sending mesh network frames.

    Connects to a peer node and sends frames using the wire protocol.
    Supports both one-shot sends and persistent connections.
    """

    def __init__(self, host, port):
        """Initialize TCP client.

        Args:
            host: Destination host address.
            port: Destination port number.

        Returns:
            None.

        Raises:
            None.
        """
        self.host = host
        self.port = port
        self.socket = None

    def connect(self):
        """Connect to remote server.

        Args:
            None.

        Returns:
            bool: True if connection successful, False otherwise.

        Raises:
            None.
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            logger.info(f'Connected to {self.host}:{self.port}')
            return True
        except Exception as e:
            logger.error(f'Connection failed: {e}')
            return False

    def send_frame(self, frame_bytes):
        """Send a frame to the remote server.

        Args:
            frame_bytes: Complete frame bytes from pack_frame.

        Returns:
            bool: True if send successful, False otherwise.

        Raises:
            None.
        """
        if not self.socket:
            return False

        try:
            self.socket.sendall(frame_bytes)
            return True
        except Exception as e:
            logger.error(f'Send failed: {e}')
            return False

    def recv_frame(self, timeout=5.0):
        """Receive a frame from the remote server.

        Args:
            timeout: Socket timeout in seconds (default 5.0).

        Returns:
            dict: Unpacked frame dictionary, or empty dict on error.

        Raises:
            None.
        """
        if not self.socket:
            return {}

        try:
            self.socket.settimeout(timeout)

            # Read 4-byte length prefix
            length_bytes = self.socket.recv(4)
            if not length_bytes:
                return {}

            length = struct.unpack('>I', length_bytes)[0]

            # Read frame body
            body = b''
            remaining = length
            while remaining > 0:
                chunk = self.socket.recv(min(remaining, 4096))
                if not chunk:
                    break
                body += chunk
                remaining -= len(chunk)

            # Unpack and return
            frame = length_bytes + body
            return unpack_frame(frame)

        except socket.timeout:
            logger.warning(f'Receive timeout from {self.host}:{self.port}')
            return {}
        except Exception as e:
            logger.error(f'Receive failed: {e}')
            return {}

    def close(self):
        """Close the connection.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        if self.socket:
            self.socket.close()
            self.socket = None
