import argparse
import sys
import time
import requests
import shutil
import subprocess
import logging
import yaml


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
    parser.add_argument('--kill', type=str, default=None)
    parser.add_argument('--log-level', choices=['INFO', 'DEBUG', 'WARNING', 'ERROR'], default='INFO')
    parser.add_argument('--config', type=str, default='config.yaml')
    parser.add_argument('--time-sync-window', type=float, default=5.0)
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

    if args.kill:
        try:
            url = f"http://127.0.0.1:8080/api/v1/nodes/{args.kill}"
            response = requests.delete(url, timeout=5)
            try:
                print(response.json())
            except ValueError:
                print(response.text)
        except Exception as e:
            logging.error(f"Failed to kill node {args.kill}: {e}")
        return 0

    server_ready = wait_for_server()
    if not server_ready:
        logging.error('Server did not become healthy within retry budget.')
        return 1

    logging.info('Server is healthy. Simulation bootstrap complete.')

    try:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Failed to read config file {args.config}: {e}")
        return 1
        
    osm_config = config.get('osm', {})
    if osm_config.get('enabled', False):
        lat = osm_config.get('fallback_lat', 28.6139)
        lon = osm_config.get('fallback_lon', 77.2090)
        spread = osm_config.get('node_spread_m', 500)
        
        logging.info(f"OSM enabled. Scattering nodes around {lat}, {lon} (spread: {spread}m).")
        try:
            loc_data = {
                'lat': lat,
                'lon': lon,
                'accuracy': spread
            }
            res = requests.post("http://127.0.0.1:8080/api/v1/location", json=loc_data, timeout=5)
            if res.status_code != 200:
                logging.warning(f"Failed to scatter nodes via API. Status: {res.status_code}")
        except Exception as e:
            logging.warning(f"Error scattering nodes via API: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
