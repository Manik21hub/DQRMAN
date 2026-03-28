"""F-10 scale benchmark for DQRMAN.

Runs a commodity-laptop load profile with 50+ nodes and reports:
- Authentication latency across node pairs
- Mesh self-heal time under load
- CPU and memory usage snapshot
- Signed/verified message throughput

Output is a structured JSON file suitable for demos and pitch decks.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import resource
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.mesh import TrustGraph
from backend.node import AuthProtocol, Node, NodeState


@dataclass
class BenchmarkConfig:
    node_count: int = 50
    auth_target_ms: float = 200.0
    heal_target_s: float = 2.0
    throughput_duration_s: float = 3.0
    throughput_workers: int = 8
    output_path: str = "logs/f10_scale_report.json"
    random_seed: int = 42


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if percentile <= 0:
        return values[0]
    if percentile >= 100:
        return values[-1]
    rank = (len(values) - 1) * (percentile / 100.0)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    w = rank - low
    return values[low] * (1.0 - w) + values[high] * w


def _memory_mb() -> float:
    # Linux ru_maxrss is reported in KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _build_nodes(node_count: int) -> list[Node]:
    nodes: list[Node] = []
    cfg = {"server": {"port": 0}}
    for _ in range(node_count):
        node = Node(cfg)
        if node.state != NodeState.ACTIVE:
            node.transition_to(NodeState.ACTIVE)
        nodes.append(node)

    # Full trust-table population for pairwise tests.
    for sender in nodes:
        for receiver in nodes:
            if sender is receiver:
                continue
            sender.trust_table[receiver.node_id] = receiver.public_key

    return nodes


def _cleanup_nodes(nodes: list[Node]) -> None:
    for node in nodes:
        try:
            node.shutdown()
        except Exception:
            # Best-effort benchmark cleanup.
            pass


def _benchmark_auth_latency(nodes: list[Node], auth_target_ms: float) -> dict:
    protocol = AuthProtocol()
    durations_ms: list[float] = []
    failures = 0

    # All unique pairs (N choose 2) approximates cross-mesh challenge latency.
    for node_a, node_b in itertools.combinations(nodes, 2):
        t0 = time.perf_counter()
        result = protocol.authenticate(node_a, node_b)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations_ms.append(elapsed_ms)
        if not result.get("success", False):
            failures += 1

    durations_ms.sort()
    p95_ms = _percentile(durations_ms, 95)
    avg_ms = statistics.mean(durations_ms) if durations_ms else 0.0
    max_ms = durations_ms[-1] if durations_ms else 0.0

    return {
        "pair_count": len(durations_ms),
        "failure_count": failures,
        "avg_ms": round(avg_ms, 3),
        "p95_ms": round(p95_ms, 3),
        "max_ms": round(max_ms, 3),
        "target_ms": auth_target_ms,
        "pass": failures == 0 and max_ms <= auth_target_ms,
    }


def _wire_mesh(nodes: list[Node], seed: int) -> TrustGraph:
    random.seed(seed)
    mesh = TrustGraph(config={"network": {"survivability_threshold": 0.2}})

    for node in nodes:
        mesh.add_node(node.node_id, node.public_key)

    # Build a ring for guaranteed baseline connectivity.
    n = len(nodes)
    for i in range(n):
        a = nodes[i].node_id
        b = nodes[(i + 1) % n].node_id
        mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
        mesh.update_edge(b, a, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    # Add random extra edges to emulate loaded mesh density.
    extra_edges = max(n * 3, 1)
    for _ in range(extra_edges):
        src, dst = random.sample(nodes, 2)
        mesh.update_edge(src.node_id, dst.node_id, auth_rate=0.8, proximity_score=1.0, recency=1.0)

    return mesh


def _benchmark_self_heal(mesh: TrustGraph, nodes: list[Node], heal_target_s: float, seed: int) -> dict:
    random.seed(seed + 17)
    active_ids = [n.node_id for n in nodes]
    kill_count = max(1, min(5, len(active_ids) // 10))
    kill_ids = random.sample(active_ids, kill_count)

    heal_durations_s: list[float] = []
    for node_id in kill_ids:
        t0 = time.perf_counter()
        mesh.on_node_failure(node_id)

        # Wait for async debounce reroute timer completion.
        deadline = time.perf_counter() + heal_target_s + 1.0
        while True:
            timer = getattr(mesh, "_reroute_timer", None)
            if timer is None or not timer.is_alive():
                break
            if time.perf_counter() > deadline:
                break
            time.sleep(0.01)

        elapsed_s = time.perf_counter() - t0
        heal_durations_s.append(elapsed_s)

    max_heal_s = max(heal_durations_s) if heal_durations_s else 0.0
    avg_heal_s = statistics.mean(heal_durations_s) if heal_durations_s else 0.0

    return {
        "kills_tested": kill_count,
        "avg_s": round(avg_heal_s, 4),
        "max_s": round(max_heal_s, 4),
        "target_s": heal_target_s,
        "pass": max_heal_s <= heal_target_s,
    }


def _benchmark_throughput(nodes: list[Node], duration_s: float, workers: int, seed: int) -> dict:
    total_signed = 0
    total_verified = 0
    total_failed = 0
    lock = threading.Lock()

    # Ensure trust tables are still fully populated after any prior phase.
    for sender in nodes:
        for receiver in nodes:
            if sender is receiver:
                continue
            sender.trust_table[receiver.node_id] = receiver.public_key

    deadline = time.perf_counter() + duration_s

    def worker(worker_index: int) -> tuple[int, int, int]:
        rng = random.Random(seed + 99 + worker_index)
        signed = 0
        verified = 0
        failed = 0
        while time.perf_counter() < deadline:
            sender, receiver = rng.sample(nodes, 2)
            packet = sender.create_signed_message(os.urandom(32), message_type="DATA")
            ok, _payload, _reason = receiver.verify_signed_message(packet)
            signed += 1
            if ok:
                verified += 1
            else:
                failed += 1
        return signed, verified, failed

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(worker, idx) for idx in range(max(1, workers))]
        for future in futures:
            s, v, f = future.result()
            with lock:
                total_signed += s
                total_verified += v
                total_failed += f
    elapsed = max(time.perf_counter() - t0, 1e-9)

    return {
        "duration_s": round(elapsed, 3),
        "workers": max(1, workers),
        "signed_total": total_signed,
        "verified_total": total_verified,
        "failed_total": total_failed,
        "messages_per_second": round(total_verified / elapsed, 2),
        "pass": total_verified > 0 and total_failed == 0,
    }


def run_benchmark(config: BenchmarkConfig) -> dict:
    if config.node_count < 50:
        raise ValueError("F-10 requires node_count >= 50")

    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    mem_start_mb = _memory_mb()

    nodes = _build_nodes(config.node_count)
    try:
        auth_metrics = _benchmark_auth_latency(nodes, config.auth_target_ms)
        mesh = _wire_mesh(nodes, config.random_seed)
        heal_metrics = _benchmark_self_heal(mesh, nodes, config.heal_target_s, config.random_seed)
        throughput_metrics = _benchmark_throughput(
            nodes,
            config.throughput_duration_s,
            config.throughput_workers,
            config.random_seed,
        )
    finally:
        _cleanup_nodes(nodes)

    wall_elapsed = max(time.perf_counter() - wall_start, 1e-9)
    cpu_elapsed = max(time.process_time() - cpu_start, 0.0)
    mem_end_mb = _memory_mb()

    cpu_percent = (cpu_elapsed / wall_elapsed) * 100.0
    resource_metrics = {
        "wall_time_s": round(wall_elapsed, 3),
        "cpu_time_s": round(cpu_elapsed, 3),
        "cpu_percent_process": round(cpu_percent, 2),
        "memory_start_mb": round(mem_start_mb, 2),
        "memory_end_mb": round(mem_end_mb, 2),
        "memory_peak_mb": round(max(mem_start_mb, mem_end_mb), 2),
    }

    overall_pass = bool(
        auth_metrics["pass"]
        and heal_metrics["pass"]
        and throughput_metrics["pass"]
    )

    return {
        "feature": "F-10 Scale Test (50+ Nodes)",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "node_count": config.node_count,
            "auth_target_ms": config.auth_target_ms,
            "heal_target_s": config.heal_target_s,
            "throughput_duration_s": config.throughput_duration_s,
            "throughput_workers": config.throughput_workers,
        },
        "metrics": {
            "authentication": auth_metrics,
            "self_heal": heal_metrics,
            "resource_usage": resource_metrics,
            "throughput": throughput_metrics,
        },
        "pass": overall_pass,
    }


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(description="Run F-10 DQRMAN scale benchmark")
    parser.add_argument("--nodes", type=int, default=50, help="Number of nodes (must be >= 50)")
    parser.add_argument("--auth-target-ms", type=float, default=200.0)
    parser.add_argument("--heal-target-s", type=float, default=2.0)
    parser.add_argument("--throughput-duration-s", type=float, default=3.0)
    parser.add_argument("--throughput-workers", type=int, default=8)
    parser.add_argument("--output", type=str, default="logs/f10_scale_report.json")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    return BenchmarkConfig(
        node_count=args.nodes,
        auth_target_ms=args.auth_target_ms,
        heal_target_s=args.heal_target_s,
        throughput_duration_s=args.throughput_duration_s,
        throughput_workers=args.throughput_workers,
        output_path=args.output,
        random_seed=args.seed,
    )


def main() -> int:
    cfg = parse_args()
    report = run_benchmark(cfg)

    out_path = Path(cfg.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"F-10 scale benchmark report written to: {out_path}")
    print(json.dumps(report["metrics"], indent=2))

    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
