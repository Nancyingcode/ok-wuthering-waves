"""Recheck recorded evidence without replacing missing measurements with estimates."""

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_ocr import digest, percentile

MIB = 1024**2


def rows(path):
    return [json.loads(line) for line in path.open(encoding='utf-8')]


def write_csv(path, records):
    fields = list(dict.fromkeys(key for row in records for key in row))
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()} for row in records)


def factory_overlap(data, start, end):
    begin = None
    for row in data:
        if row['event'] == 'factory_before':
            begin = row['elapsed']
        elif row['event'] == 'factory_returned' and begin is not None:
            if begin <= end and row['elapsed'] >= start:
                return True
            begin = None
    return False


def diagnostic_tables(path, output, label):
    data = rows(path)
    generations = []
    diagnostic = [row for row in data if row['event'] == 'diagnostic']
    for baseline in diagnostic:
        if baseline['transition'] != 'budget_baseline' or baseline['reason'] != 'model created':
            continue
        generation = baseline['generation']
        own = [row for row in diagnostic if row['generation'] == generation]
        judgments = [row for row in own if row['transition'] == 'budget_judgment']
        factory_before = next(row for row in diagnostic if row['transition'] == 'factory_before'
                              and row['target_generation'] == generation)
        factory_after = next(row for row in diagnostic if row['transition'] == 'factory_returned'
                             and row['target_generation'] == generation)
        cleanup = next((row for row in own if row['transition'] == 'cleanup_complete'), {})
        reset = next((row for row in own if row['transition'] == 'budget_baseline'
                      and row['reason'] == 'recycle finished'), {})
        generations.append({'run_id': baseline['run_id'], 'generation': generation,
                            'factory_before_private_bytes': factory_before['private_bytes'],
                            'factory_returned_private_bytes': factory_after['private_bytes'],
                            'baseline_bytes': baseline['baseline_bytes'],
                            'baseline_sampled_at': baseline['baseline_sampled_at'],
                            'baseline_reason': baseline['reason'], 'calls': len(judgments),
                            'first_four_before_bytes': [row['private_bytes'] for row in own if row['transition'] == 'call_before'],
                            'first_four_returned_bytes': [row['private_bytes'] for row in own if row['transition'] == 'call_returned'],
                            'first_four_judgments': judgments[:4],
                            'last_judgment': judgments[-1] if judgments else None,
                            'cleanup_private_bytes': cleanup.get('private_bytes'),
                            'unloaded_reset_baseline_bytes': reset.get('baseline_bytes')})
    if generations:
        write_csv(output / (label + '-baselines.csv'), generations)
    tails = []
    for row in data:
        if row['event'] != 'release_tail':
            continue
        state = (row.get('models') or [{}])[0] if label == 'managed' else row
        supported = [item for item in row.get('weak_objects', []) if item['weakref_supported']]
        tails.append({'generation': row['release_generation'], 'delay': row['target_seconds'],
                      'lateness': row['lateness_seconds'], 'private_mib': row['private_bytes'] / MIB,
                      'loaded': state.get('loaded'), 'current_generation': state.get('generation'),
                      'active_calls': state.get('active_calls'), 'recycling': state.get('recycling'),
                      'factory_overlap': factory_overlap(data, row['elapsed'], row['elapsed']) if label == 'direct' else None,
                      'weak_supported': len(supported), 'weak_alive': sum(item['alive'] for item in supported),
                      'weak_unsupported': sum(not item['weakref_supported'] for item in row.get('weak_objects', []))})
    write_csv(output / (label + '-tails.csv'), tails)
    categories = [dict(row) for row in data if row['event'] == 'memory_categories']
    if label == 'direct':
        for row in categories:
            row['factory_overlap'] = factory_overlap(data, row['elapsed'] - row['seconds'], row['elapsed'])
            row['eligible_for_unloaded_comparison'] = not row['factory_overlap']
    result = {'sha256': digest(path), 'calls': sum(row['event'] == 'recognition' for row in data),
              'run_id': data[0]['run_id'], 'seconds': data[-1]['elapsed'], 'generations': generations,
              'native_shape_counts': dict(Counter(json.dumps([row['model'], row['shapes'], row.get('input_dtypes')])
                                                  for row in data if row['event'] == 'native_inference')),
              'memory_categories': categories,
              'direct_release_boundaries': [{key: row.get(key) for key in
                                             ('elapsed', 'generation', 'private_bytes', 'active_calls', 'loaded')}
                                            for row in data if row['event'] == 'release_complete'],
              'tails': tails, 'completed': any(row['event'] == 'completed' for row in data)}
    return result


def audit(prior, output, managed=None, direct=None):
    output.mkdir(parents=True, exist_ok=False)
    records = rows(prior / 'continuous-A.jsonl')
    environment = next(row for row in records if row['event'] == 'environment')
    samples = [row for row in records if row['event'] == 'sample']
    lifecycle = [row for row in records if row['event'] == 'lifecycle']
    first_eight = []
    for generation in range(1, 9):
        created = next(row for row in lifecycle if row['transition'] == 'created' and row['generation'] == generation)
        released = next(row for row in lifecycle if row['transition'] == 'release_complete'
                        and row['generation'] == generation)
        calls = [row for row in records if row['event'] == 'recognition' and row['after']['generation'] == generation]
        nearest = min(samples, key=lambda row: abs(row['elapsed'] - created['elapsed']))
        first_eight.append({'run_id': environment['run_id'], 'generation': generation,
                            'created_monotonic': created['monotonic'], 'completed_calls': released['calls'],
                            'release_reason': released['reason'], 'active_at_release': released['active_calls'],
                            'baseline_actual_bytes': None, 'baseline_set_at_actual': None,
                            'factory_before_actual_bytes': None, 'factory_after_actual_bytes': None,
                            'judgment_private_actual_bytes': None, 'judgment_delta_actual_bytes': None,
                            'simultaneous_budget_conditions': 'not recorded', 'threshold_bytes': 512 * MIB,
                            'nearest_creation_sample_private_bytes': nearest['private_bytes'],
                            'nearest_creation_sample_offset_seconds': nearest['elapsed'] - created['elapsed'],
                            'first_four': [{'index': call['index'], 'scheduled_monotonic': call['scheduled_monotonic'],
                                            'label': call['label'], 'before': call['before'], 'after': call['after']}
                                           for call in calls[:4]],
                            'drained_event': any(row['transition'] == 'drained' and row['generation'] == generation
                                                 for row in lifecycle),
                            'release_complete_semantics': 'manager/local references cleared, gc.collect completed; not native allocator return'})
    write_csv(output / 'prior-first-eight.csv', first_eight)

    created_times = {row['generation']: row['monotonic'] for row in lifecycle if row['transition'] == 'created'}
    tails = []
    for row in records:
        if row['event'] != 'release_tail':
            continue
        state = row['models'][0] if row.get('models') else {}
        tails.append({'run_id': row['run_id'], 'release_generation': row['release_generation'],
                      'elapsed': row['elapsed'], 'delay_target': row['target_seconds'],
                      'lateness': row['lateness_seconds'], 'private_mib': row['private_bytes'] / MIB,
                      'working_set_mib': row['working_set_bytes'] / MIB,
                      'loaded': state.get('loaded'), 'active_calls': state.get('active_calls'),
                      'recycling': state.get('recycling'), 'current_generation': state.get('generation'),
                      'latest_created_at': created_times.get(state.get('generation')),
                      'pure_unloaded': bool(state) and not state['loaded'] and not state['recycling']})
    write_csv(output / 'prior-release-tails.csv', tails)

    pair_statistics = {}
    signatures = []
    for name in ('pairs-v2', 'pairs-controlled'):
        summary = json.loads((prior / (name + '-summary') / 'summary.json').read_text(encoding='utf-8'))
        differences = [pair['task_ratio_B_over_A'] - 1 for pair in summary['pairs']]
        ratio = summary['aggregate_medians']['B']['task_seconds'] / summary['aggregate_medians']['A']['task_seconds'] - 1
        pair_statistics[name] = {'pair_differences': differences,
                                 'median_paired_difference': statistics.median(differences),
                                 'ratio_of_group_medians_minus_one': ratio,
                                 'predeclared_primary_statistic': None,
                                 'analysis_status': 'post hoc exploratory; original protocol has budgets, no primary aggregate or all-pairs rule',
                                 'original_pairs': summary['pairs']}
        for member in sorted((prior / name).glob('[0-9][0-9]-[AB]')):
            data = rows(member / 'runtime.jsonl')
            native = []
            generation = 0
            call_counts = Counter()
            groups = defaultdict(list)
            for row in data:
                if row['event'] == 'lifecycle' and 'OCR_LIFECYCLE ' in row['message']:
                    event = json.loads(row['message'].split('OCR_LIFECYCLE ', 1)[1])
                    if event['event'] == 'created':
                        generation = event['generation']
                elif row['event'] == 'native_inference':
                    native.append((row['model'], row['shapes']))
                elif row['event'] == 'recognition':
                    cold = call_counts[generation] == 0
                    call_counts[generation] += 1
                    if row.get('task') == 'FarmEchoTask':
                        key = json.dumps([row['image_shape'], native, cold], sort_keys=True)
                        groups[key].append(row['seconds'])
                    native = []
            for signature, times in groups.items():
                signatures.append({'group': name, 'member': member.name,
                                   'callpoint': 'FarmEchoTask.ocr (no source line/caller stack recorded)',
                                   'image_native_shapes_cold': signature, 'calls': len(times),
                                   'p50_seconds': statistics.median(times), 'p95_seconds': percentile(times, .95)})
    write_csv(output / 'prior-pair-shape-timings.csv', signatures)

    with zipfile.ZipFile(prior / 'start-files.zip') as archive:
        initial = archive.read('src/ocr_memory.py').decode('utf-8-sig').replace('\r\n', '\n')
    with zipfile.ZipFile(prior / 'round2-files.zip') as archive:
        final = archive.read('src/ocr_memory.py').decode('utf-8-sig').replace('\r\n', '\n')
    initial_function = next(node for node in ast.parse(initial).body
                            if isinstance(node, ast.FunctionDef) and node.name == '_validate_parameter_support')
    final_function = next(node for node in ast.parse(final).body
                          if isinstance(node, ast.FunctionDef) and node.name == '_validate_parameter_support')
    lines = final.splitlines(keepends=True)
    lines[final_function.lineno - 1:final_function.end_lineno] = initial.splitlines(keepends=True)[
        initial_function.lineno - 1:initial_function.end_lineno]
    reconstructed = ''.join(lines).replace('import inspect\n', '')
    start = reconstructed.index('    def init_with_preload(self):')
    end = reconstructed.index('    def warm_default_ocr(self):')
    before = reconstructed[start:end].replace("        if options.get('lib') == 'onnxocr':\n            _validate_parameter_support(options)\n", '')
    reconstructed = reconstructed[:start] + before + reconstructed[end:]
    fingerprint = hashlib.sha256(reconstructed.encode()).hexdigest()
    expected = environment['source_sha256']
    # Exact hash matching makes the reconstruction evidence; a plausible AST alone would not.
    matched = fingerprint == expected
    if matched:
        (output / 'reconstructed-performance-ocr_memory.py').write_bytes(reconstructed.encode())
    trees = [ast.parse(source) for source in (reconstructed, final)]
    classes = [next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ManagedOCR')
               for tree in trees]
    result = {'prior': str(prior), 'inputs': {'continuous_sha256': digest(prior / 'continuous-A.jsonl'),
                                          'source_expected': expected},
              'historical_missing': ['actual budget baseline/value/time', 'factory boundary private',
                                     'judgment private/delta/all conditions', 'natural native shapes/batches',
                                     'weak object liveness', 'native allocation categories'],
              'natural_native_events': sum(row['event'] == 'native_inference' for row in records),
              'first_eight': first_eight, 'tails': tails, 'pair_statistics': pair_statistics,
              'performance_source_reconstruction': {'sha256': fingerprint, 'matches_recorded_hash': matched,
                                                     'candidate_ManagedOCR_ast_same_as_round2_final':
                                                     ast.dump(classes[0]) == ast.dump(classes[1]),
                                                     'historical_hot_path_claim_verified': True if matched else None}}
    for label, path in [('managed', managed), ('direct', direct)]:
        if path:
            result[label] = diagnostic_tables(path, output, label)
    (output / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--managed', type=Path)
    parser.add_argument('--direct', type=Path)
    args = parser.parse_args()
    result = audit(args.prior.resolve(), args.output.resolve(), args.managed, args.direct)
    print(json.dumps({'pairs': result['pair_statistics'],
                      'source': result['performance_source_reconstruction'],
                      'missing': result['historical_missing']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
