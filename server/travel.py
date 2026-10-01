"""이동시간 추정 (A05).

기본은 직선거리 × 도로 보정계수 ÷ 평균속도의 합성 추정이다. KAKAO_REST_KEY가 있으면
카카오모빌리티 길찾기를 사용할 수 있다. 지도 요청에는 출발·도착 좌표만 보내고
환자 정보는 넣지 않는다. 결과는 실제 도착시각의 보장이 아니다.
"""
import json
import math
import os
import urllib.parse
import urllib.request

ROAD_FACTOR = 1.3      # 실험 설정값
SPEED_KMH = 40.0       # 실험 설정값
KAKAO_URL = 'https://apis-navi.kakaomobility.com/v1/directions'


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def synthetic_estimate(origin, destination):
    km = haversine_km(*origin, *destination) * ROAD_FACTOR
    # 분 단위로 올림해 도착 이전 치료가 잡히지 않게 한다.
    seconds = math.ceil(km / SPEED_KMH * 60) * 60
    return dict(duration_seconds=max(seconds, 60), distance_meters=round(km * 1000),
                provider='synthetic', scenario=f'haversine x{ROAD_FACTOR} @{SPEED_KMH}km/h')


def kakao_estimate(origin, destination, key, timeout=5):
    query = urllib.parse.urlencode({'origin': f'{origin[1]},{origin[0]}',
                                    'destination': f'{destination[1]},{destination[0]}'})
    req = urllib.request.Request(f'{KAKAO_URL}?{query}', headers={'Authorization': f'KakaoAK {key}'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    route = body['routes'][0]
    if route.get('result_code') != 0:
        raise RuntimeError(f"kakao route failed: {route.get('result_msg')}")
    summary = route['summary']
    return dict(duration_seconds=math.ceil(summary['duration'] / 60) * 60,
                distance_meters=summary['distance'], provider='kakao', scenario='car')


class TravelEstimator:
    """같은 좌표 쌍은 캐시해 화면 조회마다 외부 API를 부르지 않는다."""

    def __init__(self, use_kakao=None):
        self.key = os.environ.get('KAKAO_REST_KEY')
        self.use_kakao = bool(self.key) if use_kakao is None else use_kakao
        self.cache = {}

    def estimate(self, origin, destination):
        k = (round(origin[0], 5), round(origin[1], 5), round(destination[0], 5), round(destination[1], 5))
        if k not in self.cache:
            result = None
            if self.use_kakao and self.key:
                try:
                    result = kakao_estimate(origin, destination, self.key)
                except Exception:
                    result = None   # 실패 시 합성 추정으로 대체하고 provider로 구분
            self.cache[k] = result or synthetic_estimate(origin, destination)
        return self.cache[k]
