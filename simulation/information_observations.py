"""Pure resource observations for the isolated information-quality experiment.

Freshness uses source ``observed_at``, not delivery time. This source-time
contract extends the live server's ``updated_at`` freshness semantics; it is
an experiment assumption, not an assertion that a hospital acceptance updates
resource quantities. Only an explicit observation payload calls ``apply``.

The store never reads simulation truth, a DB, a network or a response schedule.
Unknown values are never converted to zero or to an available resource count.
"""
from __future__ import annotations

from collections.abc import Mapping
import math


def _number(value, name, *, negative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + ' must be a finite number')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (not negative and value < 0):
        raise ValueError(name + ' must be finite' + ('' if negative else ' and nonnegative'))
    return value


def _quantity(value, name, *, unknown=False):
    if unknown and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(name + ' must be a nonnegative integer' + (' or None' if unknown else ''))
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(name + ' must be a nonempty string')
    return value


class ResourceObservationStore:
    """Keep the newest source stamp per hospital/resource, with copy-out views.

    Resource payloads omit unchanged resources. A present resource is either
    ``None`` (explicitly unknown), or both ``capacity`` and ``used`` fields;
    either quantity may be ``None``. Newer unknown observations supersede old
    known values. Equal and older ``(observed_at, sequence)`` stamps do nothing.
    """

    def __init__(self, stale_after_minutes=10):
        self.stale_after_minutes = _number(stale_after_minutes, 'stale_after_minutes')
        self._observations = {}

    def apply(self, hospital_id, resources, *, observed_at, received_at, now,
              sequence=0, source='feed'):
        """Validate an entire received packet, then apply its newer resources.

        Validation is atomic: an invalid quantity or future timestamp cannot
        poison one resource while another resource from the same packet changes.
        Initial observations may have a negative source time (before t=0).
        """
        hospital_id = _identifier(hospital_id, 'hospital_id')
        observed_at = _number(observed_at, 'observed_at', negative=True)
        received_at = _number(received_at, 'received_at')
        now = _number(now, 'now')
        sequence = _quantity(sequence, 'sequence')
        source = _identifier(source, 'source')
        if observed_at > received_at or received_at > now:
            raise ValueError('observation requires observed_at <= received_at <= now')
        if not isinstance(resources, Mapping):
            raise ValueError('resources must be a mapping')
        incoming = {}
        for resource, quantities in resources.items():
            resource = _identifier(resource, 'resource')
            if quantities is None:
                capacity = used = None
            else:
                if not isinstance(quantities, Mapping) or set(quantities) != {'capacity', 'used'}:
                    raise ValueError('resource requires capacity and used, or None')
                capacity = _quantity(quantities['capacity'], 'capacity', unknown=True)
                used = _quantity(quantities['used'], 'used', unknown=True)
            incoming[resource] = dict(capacity=capacity, used=used, observed_at=observed_at,
                received_at=received_at, sequence=sequence, source=source)
        changed = False
        for resource, observation in incoming.items():
            key = hospital_id, resource
            previous = self._observations.get(key)
            stamp = observation['observed_at'], observation['sequence']
            if previous is None or stamp > (previous['observed_at'], previous['sequence']):
                self._observations[key] = observation
                changed = True
        return changed

    def get(self, hospital_id, resource, *, now, committed=0, queued=0):
        """Return known arithmetic and its quality; stale arithmetic is preserved.

        ``committed`` and ``queued`` are caller-supplied current known demand,
        not quantities guessed from an old feed. Negative free values represent
        a shortage and are preserved. Unknown capacity/occupancy produces None.
        """
        hospital_id = _identifier(hospital_id, 'hospital_id')
        resource = _identifier(resource, 'resource')
        now = _number(now, 'now')
        committed = _quantity(committed, 'committed')
        queued = _quantity(queued, 'queued')
        observation = self._observations.get((hospital_id, resource))
        if observation is None:
            return dict(capacity=None, used=None, committed=committed, queued=queued, free=None,
                observed_at=None, received_at=None, sequence=None, source=None,
                status='unknown', age_minutes=None)
        if observation['received_at'] > now:
            raise ValueError('cannot read an observation before its receipt')
        view = dict(observation, committed=committed, queued=queued)
        age = now - observation['observed_at']
        known = view['capacity'] is not None and view['used'] is not None
        view.update(free=view['capacity'] - view['used'] - committed - queued if known else None,
            status=('stale' if age > self.stale_after_minutes else 'fresh') if known else 'unknown',
            age_minutes=age)
        return view

    def snapshot(self, hospital_id, resources, *, now, committed=None, queued=None):
        """Return independent views for requested resource names only."""
        hospital_id = _identifier(hospital_id, 'hospital_id')
        now = _number(now, 'now')
        if isinstance(resources, (str, bytes)):
            raise ValueError('resources must be an iterable of resource names')
        if committed is not None and not isinstance(committed, Mapping):
            raise ValueError('committed must be a resource mapping or None')
        if queued is not None and not isinstance(queued, Mapping):
            raise ValueError('queued must be a resource mapping or None')
        committed = {} if committed is None else committed
        queued = {} if queued is None else queued
        return {resource: self.get(hospital_id, resource, now=now,
                    committed=committed.get(resource, 0), queued=queued.get(resource, 0))
                for resource in resources}
