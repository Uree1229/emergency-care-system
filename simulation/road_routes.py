"""Small, cached OSRM road-routing adapter for the synthetic map replay.

Coordinates exposed here use [latitude, longitude]; OSRM's wire format uses
[longitude, latitude]. The public demo is for reasonable non-commercial use,
at most one request/second. This adapter serializes requests and waits 1.1 s
between starts. It does not claim live traffic or ambulance priority routing.
https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
https://project-osrm.org/docs/v5.24.0/api/#route-service
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_CACHE = Path(__file__).resolve().parent / 'route_cache'
DEFAULT_BASE_URL = 'https://router.project-osrm.org'
MIN_REQUEST_INTERVAL = 1.1
_THREAD_LOCK = threading.RLock()


class RoadRouteError(RuntimeError):
    """A route could not be obtained; never implies an available road route."""


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('A point must be [latitude, longitude].')
    if any(isinstance(x, bool) for x in value):
        raise ValueError('Boolean coordinates are invalid.')
    lat, lon = map(float, value)
    if not all(map(math.isfinite, (lat, lon))) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid latitude/longitude.')
    return [lat, lon]


def _nonnegative(value, field):
    if isinstance(value, bool):
        raise RoadRouteError(f'Invalid {field}.')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RoadRouteError(f'Invalid {field}.') from None
    if not math.isfinite(number) or number < 0:
        raise RoadRouteError(f'Invalid {field}.')
    return number


def distance_meters(left, right):
    """Great-circle length used only to interpolate road geometry and snaps."""
    left, right = _point(left), _point(right)
    a, b = math.radians(left[0]), math.radians(right[0])
    dlat, dlon = b - a, math.radians(right[1] - left[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


def _geometry(route_or_geometry):
    value = route_or_geometry.get('geometry') if isinstance(route_or_geometry, dict) else route_or_geometry
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError('Route geometry needs at least two points.')
    return [_point(point) for point in value]


def _ratio(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Ratio must be finite.')
    return max(0.0, min(1.0, value))


def _cut_geometry(route_or_geometry, ratio):
    points, ratio = _geometry(route_or_geometry), _ratio(ratio)
    if ratio == 0:
        return [points[0], points[0][:]]
    if ratio == 1:
        return points
    lengths = [distance_meters(a, b) for a, b in zip(points, points[1:])]
    target = sum(lengths) * ratio
    if target == 0:
        return [points[0], points[0][:]]
    prefix, done = [points[0]], 0.0
    for index, length in enumerate(lengths):
        if length == 0:
            continue
        if done + length >= target:
            fraction = (target - done) / length
            left, right = points[index], points[index + 1]
            prefix.append([left[0] + (right[0] - left[0]) * fraction,
                           left[1] + (right[1] - left[1]) * fraction])
            return prefix
        prefix.append(points[index + 1])
        done += length
    return points


def point_along(route_or_geometry, ratio):
    """Interpolate by cumulative road length, not the number of vertices.

Progress is uniform in distance, not a representation of per-segment speeds.
"""
    return _cut_geometry(route_or_geometry, ratio)[-1]


def prefix_route(route, ratio):
    """Copy and truncate a route with exactly the same endpoint as point_along.

Duration is scaled by the same fraction as distance. This is the replay's
within-leg interpolation assumption, not a new routing-engine estimate.
"""
    ratio = _ratio(ratio)
    result = copy.deepcopy(route)
    result['geometry'] = _cut_geometry(route, ratio)
    result['distance_meters'] = _nonnegative(route['distance_meters'], 'distance') * ratio
    result['duration_seconds'] = _nonnegative(route['duration_seconds'], 'duration') * ratio
    result['snapped_endpoints'] = [result['geometry'][0], result['geometry'][-1]]
    result['requested_endpoints'] = [result['geometry'][0], result['geometry'][-1]]
    result['connectors'] = []
    result['truncated_fraction'] = ratio
    result['progress_model'] = 'uniform_distance_proportional_duration'
    return result


def synthetic_route(origin, destination, reason='explicit_synthetic_mode'):
    """Explicit straight-line alternative. It is never labeled a road route."""
    origin, destination = _point(origin), _point(destination)
    distance = distance_meters(origin, destination) * 1.3
    return dict(duration_seconds=distance / (40000 / 3600), distance_meters=distance,
                geometry=[origin, destination], provider='synthetic',
                source='straight_line_times_1.3_at_40kmh', fetched_at=None,
                geometry_kind='synthetic_straight_line', requested_endpoints=[origin, destination],
                snapped_endpoints=[origin, destination], connectors=[], fallback_reason=reason,
                live_traffic=False, ambulance_priority=False)


class RoadRouteProvider:
    """Frozen route cache shared by all algorithm branches and repeat replays.

    Existing cache files are deliberately not refreshed during replay generation:
    that keeps the algorithms on the same road/time inputs. A new cache directory
    is an explicit way to collect a new routing snapshot. Network is never used
    in offline mode. Failure raises by default; fallback must be opted into.
    """

    def __init__(self, cache_dir=None, base_url=DEFAULT_BASE_URL, timeout=25,
                 allow_synthetic_fallback=False, offline=False, opener=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Routing base URL must not contain credentials or query parameters.')
        self.base_url = base_url.rstrip('/')
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('timeout must be positive.')
        self.allow_synthetic_fallback = bool(allow_synthetic_fallback)
        self.offline = bool(offline)
        self.opener = opener or urlopen

    def _key(self, origin, destination):
        # The cache key uses the exact coordinates sent on the wire.
        return dict(version=1, provider='osrm', base_url=self.base_url, profile='driving',
                    origin=[round(x, 6) for x in origin],
                    destination=[round(x, 6) for x in destination])

    @staticmethod
    def _hash(key):
        return hashlib.sha256(json.dumps(key, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def _load(self, path, key):
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value['request'] != key:
                return None
            route = value['route']
            points = _geometry(route)
            _nonnegative(route['distance_meters'], 'distance')
            _nonnegative(route['duration_seconds'], 'duration')
            if route['provider'] != 'osrm' or route['geometry_kind'] != 'road':
                return None
            if route['snapped_endpoints'] != [points[0], points[-1]]:
                return None
            return route
        except (OSError, ValueError, KeyError, TypeError, RoadRouteError):
            return None

    @contextmanager
    def _request_guard(self):
        # mkdir is atomic on Windows and Unix. The disk lock also serializes
        # independent generator processes that use the same cache directory.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        lock = self.cache_dir / '.request_lock'
        deadline = time.monotonic() + self.timeout + 120
        with _THREAD_LOCK:
            while True:
                try:
                    lock.mkdir()
                    break
                except FileExistsError:
                    try:
                        if time.time() - lock.stat().st_mtime > self.timeout + 120:
                            lock.rmdir()
                            continue
                    except (FileNotFoundError, OSError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RoadRouteError('Routing cache is busy; retry after the current collector finishes.')
                    time.sleep(0.1)
            try:
                yield
            finally:
                lock.rmdir()

    def _throttle(self):
        path = self.cache_dir / '.last_request.json'
        try:
            previous = float(json.loads(path.read_text(encoding='utf-8'))['started_at'])
        except (OSError, ValueError, KeyError, TypeError):
            previous = 0.0
        wait = MIN_REQUEST_INTERVAL - (time.time() - previous)
        # An invalid future timestamp must not freeze the collector indefinitely.
        if wait > 0:
            time.sleep(min(wait, MIN_REQUEST_INTERVAL))
        path.write_text(json.dumps({'started_at': time.time()}), encoding='utf-8')

    def _fetch(self, key):
        origin, destination = key['origin'], key['destination']
        coordinates = ';'.join(f'{p[1]:.6f},{p[0]:.6f}' for p in (origin, destination))
        url = (f'{self.base_url}/route/v1/driving/{coordinates}'
               '?overview=full&geometries=geojson&steps=false&alternatives=false&generate_hints=false')
        request = Request(url, headers={'User-Agent': 'EmergencyCareCapstoneSimulation/1.0 (non-commercial educational replay)',
                                        'Accept': 'application/json'})
        self._throttle()
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise RoadRouteError('Routing response exceeds the size limit.')
            data = json.loads(body)
        except RoadRouteError:
            raise
        except Exception as exc:
            # Error messages from HTTP clients may contain full request URLs.
            raise RoadRouteError(f'OSRM request failed ({type(exc).__name__}).') from None
        try:
            if data.get('code') != 'Ok' or not data.get('routes'):
                raise RoadRouteError('OSRM did not return a usable road route.')
            raw = data['routes'][0]
            geometry = raw['geometry']
            if geometry.get('type') != 'LineString':
                raise RoadRouteError('OSRM route geometry is not a LineString.')
            points = [_point([point[1], point[0]]) for point in geometry['coordinates']]
            points = _geometry(points)
            duration = _nonnegative(raw['duration'], 'duration')
            distance = _nonnegative(raw['distance'], 'distance')
            geom_distance = sum(distance_meters(a, b) for a, b in zip(points, points[1:]))
            if geom_distance > 1 and (duration == 0 or distance == 0):
                raise RoadRouteError('Nonzero road geometry has zero duration/distance.')
            waypoints = data['waypoints']
            snapped = [_point([w['location'][1], w['location'][0]]) for w in waypoints]
            if len(snapped) != 2 or any(distance_meters(a, b) > 5 for a, b in zip(snapped, (points[0], points[-1]))):
                raise RoadRouteError('OSRM snapped endpoints disagree with the route geometry.')
        except RoadRouteError:
            raise
        except (IndexError, KeyError, TypeError, ValueError):
            raise RoadRouteError('Invalid OSRM route response.') from None
        connectors = []
        for kind, requested, snap in zip(('origin', 'destination'), (origin, destination), snapped):
            gap = distance_meters(requested, snap)
            if gap > 0.05:
                connectors.append(dict(endpoint=kind, geometry=[requested, snap], distance_meters=gap,
                                       geometry_kind='offroad_connector', included_in_duration=False))
        return dict(duration_seconds=duration, distance_meters=distance, geometry=points,
                    provider='osrm', source=f'{self.base_url}/route/v1/driving',
                    fetched_at=datetime.now(timezone.utc).isoformat(), geometry_kind='road',
                    requested_endpoints=[origin, destination], snapped_endpoints=[points[0], points[-1]],
                    connectors=connectors, data_version=data.get('data_version'),
                    live_traffic=False, ambulance_priority=False,
                    attribution='Routing: OSRM / FOSSGIS; road data © OpenStreetMap contributors',
                    progress_model='uniform_distance_proportional_duration')

    def route(self, origin, destination):
        origin, destination = _point(origin), _point(destination)
        key = self._key(origin, destination)
        path = self.cache_dir / f'{self._hash(key)}.json'
        try:
            route = self._load(path, key)
            if route is not None:
                return copy.deepcopy(route)
            if self.offline:
                raise RoadRouteError('Road route is missing from the offline cache.')
            with self._request_guard():
                route = self._load(path, key)
                if route is None:
                    route = self._fetch(key)
                    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
                    temporary.write_text(json.dumps(dict(request=key, route=route), ensure_ascii=False,
                                                    allow_nan=False, separators=(',', ':')), encoding='utf-8')
                    temporary.replace(path)
            return copy.deepcopy(route)
        except (RoadRouteError, OSError) as exc:
            if self.allow_synthetic_fallback:
                return synthetic_route(origin, destination, reason=type(exc).__name__)
            if isinstance(exc, RoadRouteError):
                raise
            raise RoadRouteError(f'Road route cache failed ({type(exc).__name__}).') from None
