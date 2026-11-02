"""Summarize all paired attempts without dropping failures or changing budgets."""

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics

from benchmark_ocr import digest, percentile


def read_rows(path):
    return [json.loads(line) for line in path.open(encoding='utf-8')]


def summarize(directory, output):
    members = []
    for member in sorted(directory.glob('[0-9][0-9]-[AB]')):
        metadata = json.loads((member / 'run.json').read_text(encoding='utf-8'))
        record = {'member': member.name, 'label': metadata['label'],
                  'state': metadata.get('status', {}).get('state'), 'failure': metadata.get('failure'),
                  'runtime_sha256': digest(member / 'runtime.jsonl')}
        task_files = list((member / 'mcp').glob('*/memory.jsonl'))
        if len(task_files) != 1:
            record['missing'] = 'Expected exactly one task memory trace'
            members.append(record)
            continue
        task_rows = read_rows(task_files[0])
        runtime_rows = read_rows(member / 'runtime.jsonl')
        completed = next((row for row in task_rows if row['event'] == 'task_completed'), None)
        if completed is None:
            record['missing'] = 'No task_completed assertion boundary'
            members.append(record)
            continue
        samples = [row for row in task_rows if row['event'] == 'sample' and row['phase'] == 'boss_task']
        calls = [row for row in task_rows if row['event'] == 'recognition']
        record.update(task_seconds=completed['seconds'], team=completed.get('team'),
                      echo_count=completed['info'].get('Echo Count'),
                      combat_count=completed['info'].get('Combat Count'), ocr_calls=len(calls),
                      task_ocr_p95_seconds=percentile([row['seconds'] for row in calls], .95),
                      task_input_shapes=dict(Counter(str(row['image_shape']) for row in calls)),
                      peak_private_mib=max((row['private_bytes'] / 1024**2 for row in samples
                                            if row.get('private_bytes') is not None), default=None),
                      peak_working_set_mib=max((row['working_set_bytes'] / 1024**2 for row in samples), default=None))
        generation = None
        counts = Counter()
        hot = []
        cold = []
        loads = []
        for row in runtime_rows:
            if row['event'] == 'lifecycle' and 'OCR_LIFECYCLE ' in row['message']:
                event = json.loads(row['message'].split('OCR_LIFECYCLE ', 1)[1])
                if event['event'] == 'created':
                    generation = event['generation']
                    loads.append(event['seconds'])
            elif row['event'] == 'recognition':
                if counts[generation] == 0:
                    cold.append({'seconds': row['seconds'], 'task': row.get('task'), 'generation': generation})
                elif row.get('task') == 'FarmEchoTask':
                    hot.append(row['seconds'])
                counts[generation] += 1
        record.update(task_hot_ocr_p95_seconds=percentile(hot, .95), hot_ocr_calls=len(hot),
                      first_backend_calls=cold, model_load_seconds=loads,
                      native_shapes=dict(Counter(str((row['model'], row['shapes']))
                                                 for row in task_rows if row['event'] == 'native_inference')))
        ended = datetime.fromisoformat(completed['timestamp'])
        idle_samples = [row for row in runtime_rows if row['event'] == 'sample' and row.get('models')
                        and (datetime.fromisoformat(row['timestamp']) - ended).total_seconds() >= 80]
        if idle_samples:
            record['post_task'] = {'private_mib': statistics.median(row['private_bytes'] / 1024**2
                                                                    for row in idle_samples),
                                   'working_set_mib': statistics.median(row['working_set_bytes'] / 1024**2
                                                                        for row in idle_samples),
                                   'ocr_loaded': idle_samples[-1]['models'][0]['loaded'],
                                   'yolo': idle_samples[-1].get('yolo')}
        members.append(record)
    lookup = {row['member']: row for row in members}
    pairs = []
    for a, b in [('01-A', '02-B'), ('04-A', '03-B'), ('05-A', '06-B')]:
        if a not in lookup or b not in lookup or 'task_seconds' not in lookup[a] or 'task_seconds' not in lookup[b]:
            pairs.append({'A': a, 'B': b, 'status': 'unverified'})
            continue
        left, right = lookup[a], lookup[b]
        ratio = right['task_seconds'] / left['task_seconds']
        hot_ratio = (right['task_hot_ocr_p95_seconds'] / left['task_hot_ocr_p95_seconds']
                     if left.get('task_hot_ocr_p95_seconds') and right.get('task_hot_ocr_p95_seconds') else None)
        pairs.append({'A': a, 'B': b, 'task_ratio_B_over_A': ratio, 'task_within_5_percent': ratio <= 1.05,
                      'hot_p95_ratio_B_over_A': hot_ratio,
                      'hot_within_10_percent': hot_ratio <= 1.1 if hot_ratio is not None else None,
                      'same_team': left['team'] == right['team']})
    aggregate = {}
    for label in ('A', 'B'):
        subset = [row for row in members if row['label'] == label and 'task_seconds' in row]
        aggregate[label] = {'count': len(subset)}
        for key in ('task_seconds', 'peak_private_mib', 'peak_working_set_mib', 'task_hot_ocr_p95_seconds'):
            values = [row[key] for row in subset if row.get(key) is not None]
            aggregate[label][key] = statistics.median(values) if values else None
    result = {'members': members, 'pairs': pairs, 'aggregate_medians': aggregate,
              'budgets': {'task_ratio': 1.05, 'hot_ocr_p95_ratio': 1.1},
              'statistical_contract': 'No primary aggregate or all-pairs acceptance rule was predeclared; aggregate comparisons are exploratory.',
              'limitations': 'Three pairs; OCR input mix differs. Ratios do not establish pure inference speed causality.'}
    differences = [pair['task_ratio_B_over_A'] - 1 for pair in pairs if 'task_ratio_B_over_A' in pair]
    result['exploratory_task_statistics'] = {
        'paired_differences': differences,
        'median_paired_difference': statistics.median(differences) if differences else None,
        'ratio_of_group_medians_minus_one': aggregate['B']['task_seconds'] / aggregate['A']['task_seconds'] - 1
        if aggregate['A']['task_seconds'] and aggregate['B']['task_seconds'] else None,
    }
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'summary.json').open('x', encoding='utf-8') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
    columns = ['member', 'label', 'state', 'failure', 'task_seconds', 'peak_private_mib', 'peak_working_set_mib',
               'ocr_calls', 'task_ocr_p95_seconds', 'hot_ocr_calls', 'task_hot_ocr_p95_seconds', 'echo_count', 'combat_count']
    with (output / 'tasks.csv').open('x', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(members)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directory, args.output)
    print(json.dumps({'pairs': result['pairs'], 'medians': result['aggregate_medians']}, indent=2))


if __name__ == '__main__':
    main()
