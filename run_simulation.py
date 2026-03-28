import argparse
import sys
import time
import requests
import shutil
import subprocess
import logging
import os
import signal
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


def server_supports_simulation_control(base_url='http://127.0.0.1:8080'):
    """Return True when backend exposes simulation control endpoints."""
    try:
        response = requests.get(f"{base_url}/api/v1/simulation/config", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _pids_listening_on_port(port):
    """Best-effort PID discovery for processes bound to TCP port."""
    pids = set()

    lsof_path = shutil.which('lsof')
    if lsof_path:
        result = subprocess.run([lsof_path, '-ti', f'tcp:{port}'], capture_output=True, text=True, check=False)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
        return sorted(pids)

    fuser_path = shutil.which('fuser')
    if fuser_path:
        result = subprocess.run([fuser_path, f'{port}/tcp'], capture_output=True, text=True, check=False)
        # fuser often writes PID list to stderr.
        raw = f"{result.stdout} {result.stderr}"
        for token in raw.replace('\n', ' ').split():
            token = token.strip()
            if token.isdigit():
                pids.add(int(token))

    return sorted(pids)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_server_on_port(port=8080, grace_seconds=3.0):
    """Terminate process(es) bound to port, escalating to SIGKILL if required."""
    pids = _pids_listening_on_port(port)
    if not pids:
        return True

    logging.info('Stopping stale server process(es) on port %d: %s', port, ', '.join(str(pid) for pid in pids))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            logging.error('Permission denied while trying to stop PID %d on port %d', pid, port)
            return False

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        alive = [pid for pid in pids if _pid_alive(pid)]
        if not alive:
            return True
        time.sleep(0.2)

    alive = [pid for pid in pids if _pid_alive(pid)]
    if alive:
        logging.warning('Force-killing stubborn server process(es): %s', ', '.join(str(pid) for pid in alive))
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except PermissionError:
                logging.error('Permission denied while force-stopping PID %d on port %d', pid, port)
                return False

    return True


def start_server(args, base_url='http://127.0.0.1:8080'):
    """Start backend server process and wait for health endpoint."""
    logging.info('Spawning backend/server.py...')
    env = os.environ.copy()
    env['PYTHONPATH'] = os.getcwd()
    server_proc = subprocess.Popen(
        [sys.executable, 'backend/server.py', '--nodes', str(args.nodes), '--log-level', str(args.log_level).upper()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

    if not wait_for_server(base_url=base_url, max_attempts=20, delay_seconds=1):
        logging.error('Server failed to start or become healthy.')
        server_proc.terminate()
        return False

    logging.info('Server started successfully.')
    return True


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

    base_url = 'http://127.0.0.1:8080'

    # Check if server is already running.
    if not wait_for_server(base_url=base_url, max_attempts=2, delay_seconds=0.5):
        if not start_server(args, base_url=base_url):
            return 1
    else:
        logging.info('Existing server detected. Validating API capabilities...')
        if not server_supports_simulation_control(base_url=base_url):
            logging.warning('Existing backend is stale (missing simulation control endpoints). Restarting...')
            if not terminate_server_on_port(port=8080):
                logging.error('Unable to stop stale server process on port 8080.')
                return 1
            if not start_server(args, base_url=base_url):
                return 1
        else:
            logging.info('Existing server supports simulation controls. Using current instance.')

    logging.info('Simulation bootstrap complete.')

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
        
        logging.info(f"OSM enabled. Applying simulation center at {lat}, {lon} (spread: {spread}m).")
        try:
            if server_supports_simulation_control(base_url=base_url):
                sim_payload = {
                    'node_count': args.nodes,
                    'center_lat': lat,
                    'center_lon': lon,
                    'spread_m': spread,
                }
                res = requests.post(f"{base_url}/api/v1/simulation/start", json=sim_payload, timeout=8)
                if res.status_code not in (200, 202):
                    logging.warning(f"Failed to start simulation via API. Status: {res.status_code}")
            else:
                loc_data = {
                    'lat': lat,
                    'lon': lon,
                    'accuracy': spread,
                }
                res = requests.post(f"{base_url}/api/v1/location", json=loc_data, timeout=5)
                if res.status_code != 200:
                    logging.warning(f"Failed to scatter nodes via location API. Status: {res.status_code}")
        except Exception as e:
            logging.warning(f"Error applying map/simulation bootstrap: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
