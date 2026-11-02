"""Fail-closed validation for complete fixed-sequence OCR replays."""

import json
import math
import re

from scripts.ocr_observer import completion_status


def _expectations_failed(data):
    enabled = any(row.get('check_expected') is True or (isinstance(row.get('config'), dict)
                  and row['config'].get('check_expected') is True) for row in data)
    if not enabled:
        return False
    for row in data:
        if row.get('failed_expectations', 0) != 0:
            return True
        checks = row.get('expectation_checks', [])
        if not isinstance(checks, list) or any(not isinstance(check, dict) or check.get('passed') is not True
                                               for check in checks):
            return True
    return False


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def _invalid_constant(value):
    raise ValueError(f'Invalid JSON constant: {value}')


def _result(value):
    if not isinstance(value, list) or len(value) > 1:
        raise ValueError('OCR result must contain one image or be empty')
    detections = value[0] if value else []
    if not isinstance(detections, list):
        raise ValueError('OCR detections must be a list')
    for detection in detections:
        if not isinstance(detection, list) or len(detection) != 2:
            raise ValueError('Detection must contain box and text/confidence')
        box, prediction = detection
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError('Box must have four points')
        for point in box:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('Each point must have two coordinates')
            if any(type(number) not in (int, float) or not math.isfinite(number) for number in point):
                raise ValueError('Box coordinates must be finite numbers')
        if (not isinstance(prediction, list) or len(prediction) != 2 or not isinstance(prediction[0], str)
                or type(prediction[1]) not in (int, float) or not math.isfinite(prediction[1])):
            raise ValueError('Prediction must contain exact text and a finite confidence')
    return detections


def _read(path):
    result = {'calls': [], 'results': [], 'hashes': [], 'integrity_errors': [],
              'identity_errors': [], 'structure_errors': [], 'run_id': None, 'associated_native_records': 0}
    try:
        with path.open(encoding='utf-8-sig') as stream:
            data = [json.loads(line, parse_float=_finite_float, parse_constant=_invalid_constant) for line in stream]
        if any(not isinstance(row, dict) for row in data):
            raise ValueError('Every JSONL row must be an object')
    except (OSError, ValueError, OverflowError) as error:
        result['integrity_errors'].append(f'JSONL parse failed: {error}')
        return result
    run_ids = {row.get('run_id') for row in data if isinstance(row.get('run_id'), str) and row['run_id']}
    if len(run_ids) != 1 or any(not isinstance(row.get('run_id'), str) or row['run_id'] not in run_ids for row in data):
        result['integrity_errors'].append('Missing or mixed run_id')
    else:
        result['run_id'] = next(iter(run_ids))
    if any(not isinstance(row.get('event'), str) for row in data):
        result['integrity_errors'].append('Missing event')
    completions = [(index, row) for index, row in enumerate(data)
                   if row.get('event') == 'completed' or row.get('event') == 'terminal']
    if any(row.get('event') in ('failed', 'failure', 'incomplete', 'cancelled', 'error')
           or row.get('status') in ('failed', 'failure', 'incomplete', 'cancelled', 'error') for row in data):
        result['integrity_errors'].append('Explicit failed/incomplete terminal')
    if _expectations_failed(data):
        result['integrity_errors'].append('Enabled expectation validation failed or is unverified')
    calls = [(position, row) for position, row in enumerate(data) if row.get('event') == 'recognition']
    result['calls'] = [row for _, row in calls]
    if not calls or len(calls) > 10000:
        result['integrity_errors'].append('Expected 1..10000 recognition calls')
    if len(completions) != 1:
        result['integrity_errors'].append('Expected exactly one completion')
    else:
        position, completion = completions[0]
        if completion.get('status', 'success') != 'success':
            result['integrity_errors'].append('Completion is not successful')
        schema = completion.get('diagnostic_schema_version', 0)
        if type(schema) is not int or completion_status(data)['status'] == 'incomplete':
            result['integrity_errors'].append('Diagnostic completion has no healthy complete observer')
        if type(completion.get('calls')) is not int or completion['calls'] != len(calls):
            result['integrity_errors'].append('completed.calls does not match recognition count')
        if any(index > position for index, _ in calls):
            result['integrity_errors'].append('Recognition after completion')
    if [row.get('index') for _, row in calls] != list(range(len(calls))):
        result['integrity_errors'].append('index must be unique, continuous and ordered 0..N-1')
    native = {}
    for row in data:
        if (row.get('event') == 'native_inference' and row.get('index') is None
                and row.get('phase') not in ('create', 'baseline', 'startup')):
            result['identity_errors'].append('Native input identity cannot be associated with a call index')
        if row.get('event') == 'native_inference' and row.get('index') is not None:
            index = row['index']
            if type(index) is not int or not 0 <= index < len(calls):
                result['integrity_errors'].append('Native inference has an invalid index')
            else:
                native.setdefault(index, []).append(row)
                result['associated_native_records'] += 1
    for number, (_, row) in enumerate(calls):
        if not isinstance(row.get('label'), str) or not row['label'] or type(row.get('index')) is not int:
            result['integrity_errors'].append(f'Call {number}: missing label/index')
        shape = row.get('image_shape')
        if not isinstance(shape, list) or len(shape) not in (2, 3) or any(type(n) is not int or n <= 0 for n in shape):
            result['identity_errors'].append(f'Call {number}: invalid image_shape')
        sources = ([row] if 'input_sha256' in row else []) + native.get(number, [])
        hashes = []
        for source in sources:
            value = source.get('input_sha256')
            if not isinstance(value, str) or re.fullmatch(r'[0-9a-fA-F]{64}', value) is None:
                result['identity_errors'].append(f'Call {number}: input identity unverified (missing/invalid hash)')
            else:
                hashes.append(value.lower())
            if 'label' in source and source['label'] != row.get('label'):
                result['identity_errors'].append(f'Call {number}: associated native label conflicts')
        if not hashes or len(set(hashes)) != 1:
            result['identity_errors'].append(f'Call {number}: input identity unverified (missing/conflicting hashes)')
        result['hashes'].append(hashes[0] if hashes and len(set(hashes)) == 1 else None)
        try:
            result['results'].append(_result(row.get('result')))
        except (ValueError, OverflowError) as error:
            result['structure_errors'].append(f'Call {number}: {error}')
            result['results'].append(None)
    return result


def strict_compare(reference, paths):
    expected = _read(reference)
    reports = []
    for path in paths:
        actual = _read(path)
        integrity = [f'reference: {error}' for error in expected['integrity_errors']]
        integrity.extend(actual['integrity_errors'])
        identity = [f'reference: {error}' for error in expected['identity_errors']]
        identity.extend(actual['identity_errors'])
        structure = [f'reference: {error}' for error in expected['structure_errors']]
        structure.extend(actual['structure_errors'])
        if len(expected['calls']) != len(actual['calls']):
            integrity.append('Reference/candidate call counts differ')
            identity.append('Full input sequence is not verified')
        differences = []
        difference_count = 0
        maximum = 0
        for index in range(min(len(expected['calls']), len(actual['calls']))):
            old, new = expected['calls'][index], actual['calls'][index]
            if old.get('label') != new.get('label'):
                integrity.append(f'Call {index}: label differs')
            if old.get('image_shape') != new.get('image_shape') or expected['hashes'][index] != actual['hashes'][index]:
                identity.append(f'Call {index}: input identity/shape differs')
            left, right = expected['results'][index], actual['results'][index]
            if left is None or right is None:
                continue
            changed = len(left) != len(right)
            for detection in range(min(len(left), len(right))):
                old_box, old_text = left[detection]
                new_box, new_text = right[detection]
                delta = max(abs(old_box[p][axis] - new_box[p][axis]) for p in range(4) for axis in range(2))
                maximum = max(maximum, delta)
                changed |= old_text[0] != new_text[0] or delta > 2
            if changed:
                difference_count += 1
                if len(differences) < 100:
                    differences.append({'index': index, 'label': new.get('label'), 'expected': left, 'actual': right})
        output_equal = None if structure else difference_count == 0 and len(expected['calls']) == len(actual['calls'])
        reports.append({'file': str(path), 'reference': str(reference), 'scope': 'strict_fixed_sequence',
                        'reference_calls': len(expected['calls']), 'calls': len(actual['calls']),
                        'associated_native_records': {'reference': expected['associated_native_records'],
                                                      'candidate': actual['associated_native_records']},
                        'run_integrity': not integrity, 'input_identity_verified': not identity and bool(actual['calls']),
                        'output_structure_valid': not structure, 'output_equal': output_equal,
                        'equal': not (integrity or identity or structure) and bool(actual['calls']) and output_equal is True,
                        'coordinate_tolerance_px': 2, 'max_coordinate_difference_px': maximum,
                        'difference_count': difference_count, 'differences': differences,
                        'validation_error_count': len(integrity) + len(identity) + len(structure),
                        'validation_errors': {'integrity': integrity[:100], 'input_identity': identity[:100],
                                              'structure': structure[:100]}})
    return reports
