"""Bounded scalar observation of lifecycle intervals, independent of model ownership."""

from collections import deque
from contextlib import contextmanager
import queue
import threading
import time

SCHEMA_VERSION = 2


def unloaded_interval(before, after, generation):
    required = ('epoch', 'generation', 'loaded', 'active_calls', 'creating', 'recycling')
    if any(key not in state for state in (before, after) for key in required):
        return False, 'missing_versioned_state'
    if before['epoch'] != after['epoch']:
        return False, 'epoch_changed'
    if before['generation'] != generation or after['generation'] != generation:
        return False, 'release_generation_mismatch'
    if before['loaded'] or after['loaded']:
        return False, 'model_loaded'
    if before['active_calls'] or after['active_calls']:
        return False, 'active_inference'
    if before['creating'] or after['creating'] or before['recycling'] or after['recycling']:
        return False, 'lifecycle_in_progress'
    return True, None


class DiagnosticState:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(epoch=0, generation=0, loaded=False, creating=False, recycling=False, active_calls=0)

    def update(self, **fields):
        with self.lock:
            self.state.update(fields)
            self.state['epoch'] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.state)


class BoundaryObserver:
    def __init__(self, snapshot, usage, classify, emit, weak_states=lambda generation: [],
                 diagnostics=True, clock=time.monotonic, queue_limit=4096):
        self.snapshot, self.usage, self.classify, self.emit = snapshot, usage, classify, emit
        self.weak_states, self.diagnostics, self.clock = weak_states, diagnostics, clock
        self.events = queue.Queue(maxsize=queue_limit)
        self.pending, self.expected, self.observed = {}, {}, {}
        self.missing_measurements = set()
        self.errors = deque(maxlen=8)
        self.dropped = 0
        self.first_unloaded_private = None
        self.categories_taken = set()
        self.growth_seen = False
        self.target_captured = threading.Event()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closing = False
        self.deadline = None
        self.thread = None

    def _error(self, kind, message, phase):
        with self.lock:
            self.errors.append({'type': str(kind), 'message': str(message)[:300], 'phase': phase})

    def publish(self, kind, event):
        try:
            self.events.put_nowait((kind, dict(event)))
            self.wake.set()
        except queue.Full:
            with self.lock:
                self.dropped += 1
            self._error('QueueOverflow', 'Boundary event queue is full', 'publish')

    def release(self, monotonic, generation):
        self.publish('boundary', {'monotonic': monotonic, 'generation': generation})

    def start(self):
        self.thread = threading.Thread(target=self._run, name='OCRBoundaryObserver', daemon=True)
        self.thread.start()

    def set_deadline(self, deadline):
        with self.lock:
            self.deadline = deadline
        self.wake.set()

    def raise_if_failed(self):
        with self.lock:
            errors = list(self.errors)
        if errors:
            raise RuntimeError(f'OCR observer incomplete: {errors}')

    def _interval(self, generation, query):
        before = self.snapshot()
        started = self.clock()
        value = query()
        after = self.snapshot()
        finished = self.clock()
        valid, reason = unloaded_interval(before, after, generation)
        return value, {'diagnostic_schema_version': SCHEMA_VERSION,
                       'sample_started': started, 'sample_finished': finished,
                       'epoch_before': before.get('epoch'), 'epoch_after': after.get('epoch'),
                       'state_before': before, 'state_after': after,
                       'valid_for_unloaded': valid, 'invalid_reason': reason}

    def _sample(self, key, target):
        generation, delay = key
        usage, interval = self._interval(generation, self.usage)
        lifecycle_valid = interval['valid_for_unloaded']
        measurement_available = usage.get('private_bytes') is not None
        interval.update(lifecycle_valid_for_unloaded=lifecycle_valid, measurement_available=measurement_available)
        if not measurement_available:
            interval.update(valid_for_unloaded=False, invalid_reason='private_memory_unavailable')
        self.emit('release_tail', release_generation=generation, target_seconds=delay,
                  lateness_seconds=interval['sample_started'] - target, **interval, **usage,
                  weak_objects=self.weak_states(generation))
        with self.lock:
            self.observed[key] = lifecycle_valid
            if not measurement_available:
                self.missing_measurements.add(key)
        if not interval['valid_for_unloaded']:
            return
        private = usage['private_bytes']
        if self.first_unloaded_private is None:
            self.first_unloaded_private = private
        high = private - self.first_unloaded_private >= 128 * 1024**2
        category = 'high' if high else 'first_unloaded'
        if category in self.categories_taken or (not high and self.categories_taken):
            return
        classification, classified = self._interval(generation, self.classify)
        self.emit('memory_categories', category=category, release_generation=generation,
                  query_executed=True, **classified, **classification)
        if classified['valid_for_unloaded']:
            self.categories_taken.add(category)
            if high and self.growth_seen:
                self.target_captured.set()

    def step(self):
        try:
            for _ in range(self.events.maxsize):
                try:
                    kind, event = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind != 'boundary':
                    self.emit(kind, **{('transition' if key == 'event' else key): value for key, value in event.items()})
                released = kind == 'boundary' or (kind == 'lifecycle' and event.get('event') == 'release_complete')
                if released and self.diagnostics:
                    self.growth_seen |= event.get('reason') == 'private memory growth'
                    for delay in (0, 2, 10, 30):
                        key = (event['generation'], delay)
                        with self.lock:
                            if key not in self.expected:
                                if len(self.expected) >= 4 * self.events.maxsize:
                                    raise RuntimeError('Boundary target bound exceeded')
                                self.expected[key] = event['monotonic'] + delay
                                self.pending[key] = self.expected[key]
            now = self.clock()
            with self.lock:
                if self.deadline is not None and now > self.deadline:
                    return
                due = [(key, target) for key, target in self.pending.items() if target <= now]
                for key, _ in due:
                    del self.pending[key]
            for key, target in due:
                self._sample(key, target)
        except Exception as error:
            self._error(type(error).__name__, str(error), 'observe')

    def _run(self):
        while True:
            self.step()
            with self.lock:
                expired = self.deadline is not None and self.clock() >= self.deadline
                drained = self.closing and self.events.empty() and not self.pending
                failed = bool(self.errors)
            if expired or drained or failed:
                return
            self.wake.wait(.01)
            self.wake.clear()

    def health(self):
        alive = self.thread is not None and self.thread.is_alive()
        with self.lock:
            missing = sorted(set(self.expected) - set(self.observed))
            unmeasured = sorted(self.missing_measurements)
            errors = list(self.errors)
            return {'diagnostic_schema_version': SCHEMA_VERSION, 'required_tails': self.diagnostics,
                    'expected': len(self.expected), 'observed': len(self.observed),
                    'invalid': sum(not valid for valid in self.observed.values()),
                    'missing': len(missing), 'missing_targets': [list(key) for key in missing],
                    'missing_measurements': len(unmeasured),
                    'missing_measurement_targets': [list(key) for key in unmeasured],
                    'errors': errors, 'dropped_events': self.dropped, 'observer_alive': alive,
                    'complete': (not alive and not errors and not self.dropped and not missing
                                 and not unmeasured and self.events.empty())}

    def finish(self, deadline, join_timeout=5):
        with self.lock:
            self.closing = True
            self.deadline = deadline
        self.wake.set()
        if self.thread is not None:
            self.thread.join(join_timeout)
            if self.thread.is_alive():
                self._error('JoinTimeout', 'Observer did not stop within the join limit', 'shutdown')
        else:
            self.step()
        return self.health()


def completion_status(data):
    version = max((row.get('diagnostic_schema_version', 0) for row in data), default=0)
    if version < SCHEMA_VERSION:
        return {'status': 'unknown', 'diagnostic_schema_version': version or None,
                'observer_health': 'unknown', 'interval_completeness': 'unknown'}
    terminals = [row for row in data if row.get('event') in ('completed', 'incomplete', 'failed')]
    health = terminals[-1].get('observer_health', {}) if terminals else {}
    if not isinstance(health, dict):
        health = {}
    boundaries = {row['generation'] for row in data if row.get('event') == 'release_complete'
                  or (row.get('event') == 'lifecycle' and row.get('transition') == 'release_complete')}
    expected = {(generation, delay) for generation in boundaries for delay in (0, 2, 10, 30)} if health.get('required_tails') else set()
    tails = [row for row in data if row.get('event') == 'release_tail']
    keys = [(row.get('release_generation'), row.get('target_seconds')) for row in tails]
    # Recheck raw values so an older or forged healthy terminal cannot hide an
    # unavailable required measurement. Lifecycle-invalid measured points remain usable evidence.
    unmeasured = [(row.get('release_generation'), row.get('target_seconds')) for row in tails
                  if row.get('private_bytes') is None or row.get('measurement_available') is False]
    measurements_complete = not unmeasured
    measurement_summary_valid = health.get('missing_measurements', len(unmeasured)) == len(unmeasured)
    coverage = (len(keys) == len(set(keys)) and set(keys) == expected
                and health.get('expected') == len(expected) and health.get('observed') == len(keys)
                and health.get('invalid') == sum(row.get('lifecycle_valid_for_unloaded',
                                                        row.get('valid_for_unloaded')) is False for row in tails)
                and health.get('missing') == 0 and health.get('dropped_events') == 0
                and health.get('errors') == [] and health.get('observer_alive') is False)
    healthy = (len(terminals) == 1 and terminals[0].get('event') == 'completed'
               and terminals[0].get('status') == 'success'
               and health.get('complete') is True and coverage and measurements_complete and measurement_summary_valid)
    return {'status': 'success' if healthy else 'incomplete', 'diagnostic_schema_version': version,
            'observer_health': terminals[-1].get('observer_health', 'missing') if terminals else 'missing',
            'recorded_target_coverage_valid': coverage,
            'required_measurements_complete': measurements_complete, 'missing_measurements': len(unmeasured)}


@contextmanager
def observed_run(trace, observer, calls):
    try:
        yield
    except Exception as error:
        health = observer.health() if observer.closing else observer.finish(observer.clock())
        trace.emit('incomplete', diagnostic_schema_version=SCHEMA_VERSION, status='incomplete', calls=calls(),
                   observer_health=health, error={'type': type(error).__name__, 'message': str(error)[:300]})
        raise


def complete_recording(trace, observer, calls, deadline, producers_stopped=True):
    health = observer.finish(deadline)
    if not producers_stopped or not health['complete']:
        raise RuntimeError(f'Incomplete OCR diagnostic: producers_stopped={producers_stopped}; {health}')
    trace.emit('completed', diagnostic_schema_version=SCHEMA_VERSION, status='success', calls=calls,
               observer_health=health)
