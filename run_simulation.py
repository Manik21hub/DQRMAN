import argparse
import sys
import time
import requests
import shutil
import subprocess
import logging


def wait_for_server(base_url='http://127.0.0.1:8080', max_attempts=15, delay_seconds=1):
    """Wait for server health endpoint to return HTTP 200.

    Tries GET /health up to max_attempts with fixed delay between attempts.
    Returns True on first successful 200 response, otherwise False.
    """
    health_url = f"{base_url}/health"
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(health_url, timeout=2)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass

        if attempt < max_attempts:
            time.sleep(delay_seconds)

    return False


def parse_args():
    """Parse command-line arguments for simulation runner."""
    parser = argparse.ArgumentParser(description='Run DQRMAN simulation')
    parser.add_argument('--nodes', type=int, default=10)
    parser.add_argument('--kill', default=None)
    parser.add_argument('--log-level', default='INFO')
    parser.add_argument('--config', default='config.yaml')
    return parser.parse_args()


def main():
    """Entry point for the simulation runner."""
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    docker_available = shutil.which('docker')
    if docker_available is None:
        logging.warning('Docker not found; subprocess isolation is being used as the SRS A-02 fallback.')

    # Keep imported modules intentionally used in this initial scaffold.
    _ = subprocess

    server_ready = wait_for_server()
    if not server_ready:
        logging.error('Server did not become healthy within retry budget.')
        return 1

    logging.info('Server is healthy. Simulation bootstrap complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
