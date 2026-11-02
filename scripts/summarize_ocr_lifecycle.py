"""Export scalar lifecycle evidence, comparable stages and separate memory curves."""

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_ocr import digest, percentile
from scripts.ocr_observer import completion_status

MIB = 1024 ** 2


def summarize(path, output, plot=False):
    rows = [json.loads(line) for line in path.open(encoding='utf-8')]
    generations = {}
    observations = []
    tails = []
    calls = []
    for row in rows:
        if row['event'] == 'lifecycle':
            generation = generations.setdefault(row['generation'], {'generation': row['generation']})
            if row['transition'] == 'created':
                generation.update(created_elapsed=row['elapsed'], created_monotonic=row['created_at'],
                                  load_seconds=row['seconds'])
            elif row['transition'] == 'release_complete':
                generation.update(released_elapsed=row['elapsed'], reason=row['reason'],
                                  completed_calls=row['calls'], age_seconds=row['age_seconds'],
                                  release_seconds=row['seconds'], active_at_release=row['active_calls'])
        elif row['event'] == 'recognition':
            generation_id = row['after']['generation']
            generation = generations.setdefault(generation_id, {'generation': generation_id})
            generation.setdefault('_calls', []).append(row)
            calls.append(row)
        elif row['event'] in ('sample', 'release_tail'):
            models = row.get('models', [])
            model = row.get('state_after', models[0] if models else {})
            point = {key: row.get(key) for key in ('elapsed', 'phase', 'private_bytes', 'working_set_bytes')}
            point.update({key: model.get(key) for key in
                          ('generation', 'loaded', 'active_calls', 'calls', 'created_at', 'model_age_seconds')})
            if row['event'] == 'release_tail':
                point.update({key: row[key] for key in
                              ('release_generation', 'target_seconds', 'lateness_seconds')})
                if row.get('diagnostic_schema_version', 0) >= 2:
                    point['pure_unloaded'] = row.get('valid_for_unloaded') is True
                    point['interval_validity'] = row.get('valid_for_unloaded', 'unknown')
                    point['invalid_reason'] = row.get('invalid_reason')
                else:
                    point['pure_unloaded'] = bool(models) and not model['loaded'] and not model['recycling']
                    point['interval_validity'] = 'unknown'
                tails.append(point)
            else:
                observations.append(point)

    def median_metric(points, metric):
        values = [row[metric] / MIB for row in points if row.get(metric) is not None]
        return statistics.median(values) if values else None

    for point in tails:
        # An unloaded snapshot has no current model age. Join the immutable
        # creation event rather than treat the recycle budget reset as creation.
        point['latest_created_at'] = generations.get(point['generation'], {}).get('created_monotonic')
        point['released_model_created_at'] = generations.get(point['release_generation'], {}).get('created_monotonic')

    for generation_id, generation in generations.items():
        own_calls = generation.pop('_calls', [])
        hot = [row['seconds'] for row in own_calls if row['before']['calls'] > 0 and row['before']['loaded']]
        steady = [row for row in observations if row['generation'] == generation_id and row['loaded']
                  and row.get('model_age_seconds') is not None and 10 <= row['model_age_seconds'] <= 100
                  and row['phase'] == 'continuous']
        generation.update(recorded_calls=len(own_calls), hot_p95_seconds=percentile(hot, .95),
                          first_inference_seconds=own_calls[0]['seconds'] if own_calls else None,
                          steady_samples=len(steady),
                          steady_private_mib=median_metric(steady, 'private_bytes'),
                          steady_working_set_mib=median_metric(steady, 'working_set_bytes'))

    hot = [row['seconds'] for row in calls if row['before']['loaded'] and row['before']['calls'] > 0]
    generations = [generations[key] for key in sorted(generations)]
    protocol = completion_status(rows)
    completed = None
    if protocol['status'] != 'unknown':
        completed = protocol['status'] == 'success'
    summary = {'file': str(path), 'sha256': digest(path), 'diagnostic_protocol': protocol,
               'completed': completed,
               'last_elapsed': rows[-1]['elapsed'], 'calls': len(calls),
               'hot_p50_seconds': percentile(hot, .5), 'hot_p95_seconds': percentile(hot, .95),
               'scheduling_lag_p95_seconds': percentile([row['scheduling_lag_seconds'] for row in calls], .95),
               'scheduling_lag_max_seconds': max((row['scheduling_lag_seconds'] for row in calls), default=None),
               'continuous_started': min((row['elapsed'] for row in rows if row['phase'] == 'continuous'), default=None),
               'cooldown_started': next((row['elapsed'] for row in rows if row['event'] == 'cooldown_start'), None),
               'peaks_mib': {key: max((row[key] / MIB for row in observations if row.get(key) is not None), default=None)
                             for key in ('private_bytes', 'working_set_bytes')},
               'generations': generations, 'release_tails': tails,
               'shutdown': next((row for row in rows if row['event'] == 'shutdown'), None)}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'summary.json').open('x', encoding='utf-8') as target:
        json.dump(summary, target, ensure_ascii=False, indent=2)
    for name, records in [('samples', observations), ('generations', generations), ('release-tails', tails)]:
        keys = sorted({key for row in records for key in row})
        with (output / f'{name}.csv').open('x', encoding='utf-8', newline='') as target:
            writer = csv.DictWriter(target, keys)
            writer.writeheader()
            writer.writerows(records)
    if plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        for key, label in [('private_bytes', 'Private memory (MiB)'),
                           ('working_set_bytes', 'Working set (MiB)')]:
            figure, axes = plt.subplots(figsize=(13, 4))
            points = [row for row in observations if row.get(key) is not None]
            axes.plot([row['elapsed'] / 60 for row in points], [row[key] / MIB for row in points], lw=.8)
            for generation in generations:
                if 'created_elapsed' in generation:
                    axes.axvline(generation['created_elapsed'] / 60, color='green', alpha=.2, lw=.6)
                if 'released_elapsed' in generation:
                    axes.axvline(generation['released_elapsed'] / 60, color='red', alpha=.3, lw=.6)
            cooldown = summary['cooldown_started']
            if cooldown is not None:
                axes.axvspan(cooldown / 60, rows[-1]['elapsed'] / 60, color='gray', alpha=.2, label='Cooldown')
                axes.legend()
            axes.set(xlabel='Elapsed minutes', ylabel=label, title=path.stem)
            axes.grid(alpha=.2)
            figure.tight_layout()
            figure.savefig(output / f'{key}.svg')
            plt.close(figure)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    result = summarize(args.input, args.output, args.plot)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('generations', 'release_tails')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
