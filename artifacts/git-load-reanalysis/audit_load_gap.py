"""Read-only source/evidence audit; no Kubernetes or new performance run.

Descriptives and indicative serialization break-even arithmetic only.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics


def mean(values):
    return statistics.mean(list(values))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--live', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    repo, live, out = Path(a.repo), Path(a.live), Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    base = live / 'artifacts/topology-profile-live'
    inputs = [base / 'pod-pairs.json', repo / 'experiments/simulation-20261019/results/summary.csv']
    pairs = json.loads(inputs[0].read_text())['pairs']
    payloads = {}
    for name, ps in [('calibration20', [p for p in pairs if p['split'] == 'calibration']),
                     ('evaluation40', [p for p in pairs if p['split'] == 'evaluation']),
                     ('smoke_evaluation5', [p for p in pairs if p['split'] == 'evaluation'][:5])]:
        decoded_image = mean(p['image_dimensions'][0] * p['image_dimensions'][1] * 3 for p in ps)
        decoded_audio = mean(p['audio_info']['frames'] * 4 for p in ps)
        image, audio = mean(p['image_bytes'] for p in ps), mean(p['audio_bytes'] for p in ps)
        payloads[name] = dict(n=len(ps), jpeg_bytes=image, flac_bytes=audio,
            decoded_uint8_image_bytes=decoded_image, decoded_float32_audio_bytes=decoded_audio,
            decoded_to_file_image_ratio=decoded_image/image, decoded_to_file_audio_ratio=decoded_audio/audio,
            decoded_binary_input_Mbit_per_request=8*(decoded_image+decoded_audio)/1e6,
            compressed_base64_body_Mbit_per_request=8*(image+audio)*4/3/1e6,
            boundary='Payload approximation excludes binary headers/HTTP/ACK; not measured NIC throughput')
    observed = {}
    for name in ['tp-20261019t092957', 'tp-20261019t093419']:
        path = base / name / 'attempts.jsonl'
        inputs.append(path)
        rows = [r for r in map(json.loads, path.read_text().splitlines()) if not r['warmup']]
        assert len(rows) == 10 and all(r['status'] == 'ok' for r in rows)
        stages = {}
        for task in ['vision', 'speech', 'fusion']:
            rs = [next(x for x in r['receipts'] if x['task'] == task) for r in rows]
            stages[task] = dict(compute_mean_ms=mean(r['compute_ms'] for r in rs),
                                queue_mean_ms=mean(r['queue_ms'] for r in rs),
                                output_mean_bytes=mean(r['output_bytes'] for r in rs))
        residuals = []
        for r in rows:
            rs = {x['task']: x for x in r['receipts']}
            residuals.append(r['e2e_ms'] - max(rs[t]['compute_ms'] + rs[t]['queue_ms'] for t in ['vision', 'speech'])
                             - rs['fusion']['compute_ms'] - rs['fusion']['queue_ms'])
        observed[name] = dict(n=10, mean_e2e_ms=mean(r['e2e_ms'] for r in rows), stages=stages,
                             noncompute_critical_residual_mean_ms=mean(residuals),
                             residual_boundary='Executor/transfer/control/branch overlap residual, never pure network time',
                             independent_environment_replicates=1)
    thresholds = []
    p = payloads['smoke_evaluation5']
    for task, compressed, raw in [('vision', p['jpeg_bytes'], p['decoded_uint8_image_bytes']),
                                   ('speech', p['flac_bytes'], p['decoded_float32_audio_bytes'])]:
        edge = observed['tp-20261019t092957']['stages'][task]['compute_mean_ms']
        cloud = observed['tp-20261019t093419']['stages'][task]['compute_mean_ms']
        gain_s = (edge-cloud)/1000
        output = observed['tp-20261019t092957']['stages'][task]['output_mean_bytes']
        for encoding, size, factor in [('compressed_base64', compressed, 4/3), ('decoded_binary', raw, 1)]:
            thresholds.append(dict(task=task, representation=encoding, indicative_break_even_Mbps=8*(size-output)*factor/gain_s/1e6,
                                   observed_compute_gain_ms=gain_s*1000,
                                   caveat='Single-task no-queue approximation from sequential smoke. Raw moves decoding boundary, so fresh calibration required. Not DAG optimum.'))
    with inputs[1].open() as handle:
        simulation = list(csv.DictReader(handle))
    selected = [{k: r[k] for k in ['name', 'improvement_pct', 'links_improvement_pct', 'queue_improvement_pct',
                                  'mandatory_cloud_load_lower_bound']} for r in simulation
                if r['name'] in ['reference', 'light_cloud', 'steady', 'burst', 'steady_burst', 'slow_wan',
                                 'high_rtt', 'split_cloud', 'overload']]
    offered = []
    demand = payloads['evaluation40']['decoded_binary_input_Mbit_per_request']
    for bandwidth in [100, 10, 2]:
        for rate in [.1, .5, 1]:
            offered.append(dict(nominal_WAN_Mbps=bandwidth, arrival_rate_rps=rate,
                                all_cloud_input_only_offered_load=rate*demand/bandwidth,
                                caveat='Input serialization load only; headers/other traffic/return paths excluded. Emulated cap must be achievable.'))
    source_paths = [repo / 'experiments/simulation-20261019/scenarios.py', repo / 'experiments/simulation-20261019/components.py',
                    repo / 'experiments/simulation-20261019/run_suite.py', live / 'experiments/topology-profile-20261019/run.py',
                    live / 'experiments/real-topology-live/http_runtime.py', live / 'experiments/real-topology-live/edge_client.py',
                    live / 'experiments/real-topology-live/run_smoke.py',
                    repo / 'experiments/simulation-20261019/data/jellybean/VQA/python/models_profiler/compute_message_size.py']
    result = dict(scope='Archived code/observed-data reanalysis only; no new performance or network experiment',
                  payloads=payloads, observed_smoke=observed, indicative_thresholds=thresholds,
                  simulation_ablation=selected, offered_load=offered,
                  input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs+source_paths})
    assert payloads['evaluation40']['decoded_uint8_image_bytes'] == 821505
    assert payloads['evaluation40']['decoded_float32_audio_bytes'] == 108209.6
    (out / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'payloads': payloads, 'thresholds': thresholds, 'scope': result['scope']}, indent=2))


if __name__ == '__main__':
    main()
