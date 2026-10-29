"""Kakao Mobility ordinary-car directions for local simulation playback.

Only coordinates and car routing options leave the process. The REST key is
read from the process environment and sent in the Authorization header; neither
the key nor the raw response/error is persisted. Cached geometry/time supports
this app's replay UX, expires after 30 minutes, and is never a routing database.
https://developers.kakaomobility.com/guide/navi-api/directions
https://developers.kakao.com/terms/latest/ko/site-policies
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request

from .road_routes import (RoadRouteError, RoadRouteProvider, _geometry,
                          _nonnegative, _point, distance_meters)


KAKAO_URL = 'https://apis-navi.kakaomobility.com/v1/directions'
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / '.codex-build/kakao-route-cache'
DEFAULT_CACHE_SECONDS = 1800
MAX_JOIN_GAP_METERS = 2


def _timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timestamp must contain a time zone.')
    return stamp.timestamp()


class KakaoRouteProvider(RoadRouteProvider):
    """Same geometry contract as OSRM, with no synthetic or cross-provider fallback.

    Offline generation only accepts unexpired entries, so an old traffic
    snapshot cannot silently masquerade as a newly collected route. Existing
    exported playback remains historical and requires no key or network.
    """
    provider_name = 'kakao'

    def __init__(self, cache_dir=None, *, key=None, timeout=25, offline=False,
                 opener=None, cache_max_age_seconds=DEFAULT_CACHE_SECONDS):
        super().__init__(cache_dir=cache_dir if cache_dir is not None else DEFAULT_CACHE,
                         base_url='https://apis-navi.kakaomobility.com', timeout=timeout,
                         offline=offline, opener=opener, allow_synthetic_fallback=False)
        self._api_key = key if key is not None else os.environ.get('KAKAO_REST_KEY', '')
        self.cache_max_age_seconds = float(cache_max_age_seconds)
        if not math.isfinite(self.cache_max_age_seconds) or not 0 < self.cache_max_age_seconds <= 86400:
            raise ValueError('Kakao cache age must be positive and at most 24 hours.')
        self.requests_completed = 0

    def _key(self, origin, destination):
        return dict(version=1, provider='kakao', endpoint=KAKAO_URL,
                    origin=[round(x, 6) for x in origin],
                    destination=[round(x, 6) for x in destination],
                    priority='TIME', car_type=1, summary=False,
                    alternatives=False, road_details=False, roadevent=0)

    def _load(self, path, key):
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value['request'] != key:
                return None
            route = value['route']
            points = _geometry(route)
            duration = _nonnegative(route['duration_seconds'], 'duration')
            distance = _nonnegative(route['distance_meters'], 'distance')
            geometry_distance = sum(distance_meters(a, b) for a, b in zip(points, points[1:]))
            if geometry_distance > 1 and (duration == 0 or distance == 0):
                return None
            if route['provider'] != 'kakao' or route['geometry_kind'] != 'road':
                return None
            if route['snapped_endpoints'] != [points[0], points[-1]]:
                return None
            if route['requested_endpoints'] != [key['origin'], key['destination']]:
                return None
            age = datetime.now(timezone.utc).timestamp() - _timestamp(route['fetched_at'])
            if not -300 <= age <= self.cache_max_age_seconds:
                return None
            if route.get('ambulance_priority') is not False:
                return None
            return route
        except (OSError, ValueError, KeyError, TypeError, RoadRouteError):
            return None

    def _fetch(self, key):
        if not self._api_key or not self._api_key.strip():
            raise RoadRouteError('KAKAO_REST_KEY is not configured for this process.')
        origin, destination = key['origin'], key['destination']
        query = urlencode(dict(origin=f'{origin[1]:.6f},{origin[0]:.6f}',
                               destination=f'{destination[1]:.6f},{destination[0]:.6f}',
                               priority='TIME', car_type=1, summary='false',
                               alternatives='false', road_details='false', roadevent=0))
        request = Request(f'{KAKAO_URL}?{query}', headers={
            'Authorization': f'KakaoAK {self._api_key}', 'Accept': 'application/json'})
        self._throttle()
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise RoadRouteError('Kakao route response exceeds the size limit.')
            data = json.loads(body)
        except RoadRouteError:
            raise
        except Exception as exc:
            # Do not stringify HTTP exceptions: custom clients can echo headers.
            status = getattr(exc, 'code', None)
            label = f' HTTP {status}' if isinstance(status, int) else ''
            raise RoadRouteError(f'Kakao directions request failed{label} ({type(exc).__name__}).') from None
        try:
            raw = data['routes'][0]
            if isinstance(raw.get('result_code'), bool) or raw.get('result_code') != 0:
                code = raw.get('result_code')
                suffix = f' (code {code})' if isinstance(code, int) else ''
                raise RoadRouteError(f'Kakao did not return a usable road route{suffix}.')
            summary = raw['summary']
            duration = _nonnegative(summary['duration'], 'duration')
            distance = _nonnegative(summary['distance'], 'distance')
            summary_points = [_point([summary[name]['y'], summary[name]['x']])
                              for name in ('origin', 'destination')]
            points = []
            sections = raw['sections']
            if not isinstance(sections, list) or not sections:
                raise RoadRouteError('Kakao route sections are missing.')
            for section in sections:
                roads = section['roads']
                if not isinstance(roads, list) or not roads:
                    raise RoadRouteError('Kakao road geometry is missing.')
                for road in roads:
                    vertices = road['vertexes']
                    if not isinstance(vertices, list) or len(vertices) < 4 or len(vertices) % 2:
                        raise RoadRouteError('Invalid Kakao road vertex pairs.')
                    chunk = [_point([vertices[i + 1], vertices[i]])
                             for i in range(0, len(vertices), 2)]
                    if points:
                        if distance_meters(points[-1], chunk[0]) > MAX_JOIN_GAP_METERS:
                            raise RoadRouteError('Kakao road segments are disconnected; export refused.')
                        if points[-1] == chunk[0]:
                            chunk = chunk[1:]
                    points.extend(chunk)
            points = _geometry(points)
            geometry_distance = sum(distance_meters(a, b) for a, b in zip(points, points[1:]))
            if geometry_distance > 1 and (duration == 0 or distance == 0):
                raise RoadRouteError('Nonzero Kakao road geometry has zero duration/distance.')
            # Summary points may include an entrance connector and differ slightly
            # from the first/last road vertex. Large disagreement is invalid data.
            if any(distance_meters(a, b) > 200 for a, b in zip(summary_points, (points[0], points[-1]))):
                raise RoadRouteError('Kakao summary endpoints disagree with road geometry.')
        except RoadRouteError:
            raise
        except (IndexError, KeyError, TypeError, ValueError):
            raise RoadRouteError('Invalid Kakao directions response.') from None
        connectors = []
        for name, requested, snap in zip(('origin', 'destination'), (origin, destination), (points[0], points[-1])):
            gap = distance_meters(requested, snap)
            if gap > .05:
                connectors.append(dict(endpoint=name, geometry=[requested, snap],
                    distance_meters=gap, geometry_kind='offroad_connector', included_in_duration=False))
        self.requests_completed += 1
        return dict(provider='kakao', source=KAKAO_URL, geometry_kind='road',
                    geometry=points, duration_seconds=duration, distance_meters=distance,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    requested_endpoints=[origin, destination], snapped_endpoints=[points[0], points[-1]],
                    connectors=connectors, live_traffic=False, ambulance_priority=False,
                    traffic_basis='kakao_ordinary_car_estimate_at_fetch_time',
                    traffic_snapshot=True, cache_max_age_seconds=self.cache_max_age_seconds,
                    priority='TIME', attribution='경로·조회시점 자동차 예상시간: Kakao Mobility',
                    progress_model='uniform_distance_proportional_duration')


def snapshot_summary(snapshot, scenario_only=False):
    """Correct provider metadata without changing the frozen replay engine."""
    result = snapshot.summary(scenario_only=scenario_only)
    provider_name = getattr(snapshot.provider, 'provider_name', 'osrm')
    result.update(provider=provider_name)
    if provider_name == 'kakao':
        values = ([snapshot.routes[k] for k in snapshot.used_keys]
                  if scenario_only else list(snapshot.routes.values()))
        stamps = sorted(r['fetched_at'] for r in values if r.get('fetched_at'))
        result.update(live_traffic=False, traffic_snapshot=True,
                      traffic_basis='kakao_ordinary_car_estimate_at_fetch_time',
                      snapshot_first_fetched_at=stamps[0] if stamps else None,
                      snapshot_last_fetched_at=stamps[-1] if stamps else None,
                      ambulance_priority=False)
    return copy.deepcopy(result)
