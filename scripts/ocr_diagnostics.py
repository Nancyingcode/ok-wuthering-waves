"""Bounded, opt-in observers; weak callbacks never acquire model ownership."""

from collections import OrderedDict
import ctypes
from ctypes import wintypes
from functools import partial
import threading
import time
import weakref


_states = OrderedDict()
_references = {}
_lock = threading.Lock()
MAX_OBJECTS = 512


def _finalized(tag, reference):
    with _lock:
        if tag in _states:
            _states[tag]['finalized_at'] = time.monotonic()
            _states[tag]['alive'] = False


def watch_model(model, generation):
    objects = [('outer', model)]
    for name in ('text_detector', 'text_recognizer', 'text_classifier'):
        predictor = getattr(model, name, None)
        if predictor is not None:
            objects.extend([(name, predictor), (name + '.queue', getattr(predictor, '_async_queue', None)),
                            (name + '.compiled', getattr(predictor, 'session', None))])
    for kind, obj in objects:
        if obj is None:
            continue
        tag = (generation, kind, id(obj))
        row = {'generation': generation, 'kind': kind, 'object_id': id(obj),
               'type': type(obj).__module__ + '.' + type(obj).__qualname__,
               'registered_at': time.monotonic(), 'alive': True, 'finalized_at': None}
        with _lock:
            while len(_states) >= MAX_OBJECTS:
                oldest, _ = _states.popitem(last=False)
                _references.pop(oldest, None)
            try:
                _references[tag] = weakref.ref(obj, partial(_finalized, tag))
                row['weakref_supported'] = True
            except TypeError:
                row.update(weakref_supported=False, alive=None)
            _states[tag] = row


def object_states(generation=None):
    with _lock:
        return [dict(row) for row in _states.values()
                if generation is None or row['generation'] == generation]


def virtual_memory_categories():
    """Classify virtual regions, without reading pages or attributing heap owners."""
    class Region(ctypes.Structure):
        _fields_ = [('base', ctypes.c_void_p), ('allocation', ctypes.c_void_p),
                    ('allocation_protect', wintypes.DWORD), ('partition', wintypes.WORD),
                    ('size', ctypes.c_size_t), ('state', wintypes.DWORD),
                    ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]

    query = ctypes.WinDLL('kernel32', use_last_error=True).VirtualQuery
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    query.restype = ctypes.c_size_t
    totals = {'private_committed': 0, 'mapped_committed': 0, 'image_committed': 0,
              'other_committed': 0, 'reserved': 0, 'free': 0}
    allocations = {}
    address = 0
    count = 0
    started = time.perf_counter()
    while count < 50000:
        region = Region()
        if not query(address, ctypes.byref(region), ctypes.sizeof(region)):
            error = ctypes.get_last_error()
            if error != 87:
                raise ctypes.WinError(error)
            break
        count += 1
        if region.state == 0x1000:
            key = {0x20000: 'private_committed', 0x40000: 'mapped_committed',
                   0x1000000: 'image_committed'}.get(region.kind, 'other_committed')
            totals[key] += region.size
            if region.kind == 0x20000:
                allocations[region.allocation] = allocations.get(region.allocation, 0) + region.size
        elif region.state == 0x2000:
            totals['reserved'] += region.size
        elif region.state == 0x10000:
            totals['free'] += region.size
        following = (region.base or 0) + region.size
        if following <= address:
            raise RuntimeError('VirtualQuery did not advance')
        address = following
    else:
        raise RuntimeError('VirtualQuery region bound reached')
    return {'tool': 'Windows VirtualQuery', 'region_count': count, 'bytes': totals,
            'largest_private_allocations_bytes': sorted(allocations.values(), reverse=True)[:12],
            'seconds': time.perf_counter() - started,
            'limits': 'Virtual allocation categories, not allocator/live heap attribution; COW image pages retain image type'}
