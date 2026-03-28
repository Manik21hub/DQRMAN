import json
from pathlib import Path

import pytest

from scripts.scale_benchmark import BenchmarkConfig, run_benchmark


def test_f10_requires_at_least_50_nodes():
    cfg = BenchmarkConfig(node_count=49)
    with pytest.raises(ValueError, match='node_count >= 50'):
        run_benchmark(cfg)


def test_f10_report_structure_with_50_nodes(tmp_path):
    cfg = BenchmarkConfig(
        node_count=50,
        auth_target_ms=200.0,
        heal_target_s=2.0,
        throughput_duration_s=0.5,
        throughput_workers=4,
        output_path=str(tmp_path / 'f10_report.json'),
        random_seed=123,
    )

    report = run_benchmark(cfg)
    out_path = Path(cfg.output_path)
    out_path.write_text(json.dumps(report, indent=2), encoding='utf-8')

    loaded = json.loads(out_path.read_text(encoding='utf-8'))
    assert loaded['feature'].startswith('F-10 Scale Test')
    assert loaded['config']['node_count'] == 50

    metrics = loaded['metrics']
    assert 'authentication' in metrics
    assert 'self_heal' in metrics
    assert 'resource_usage' in metrics
    assert 'throughput' in metrics

    assert metrics['authentication']['pair_count'] == 1225
    assert metrics['authentication']['target_ms'] == 200.0
    assert metrics['self_heal']['target_s'] == 2.0
    assert metrics['throughput']['signed_total'] >= metrics['throughput']['verified_total']
