"""관측 기반 문의 정책 V3의 순수 설정과 제한 대기 규칙.

이 값은 합성 실험으로 비교할 개발 후보다. 수용 확률·자원 회복 시각이나
임상적 허용 대기시간을 예측하지 않는다. 병원의 실제 문의 상태도 바꾸지 않는다.
비용 계산은 호출자가 V2와 같은 관측 계산으로 제공하며 DB·서비스는 참조하지 않는다.
"""
from copy import deepcopy
import json
from pathlib import Path
import re


V3_EXPERIMENT_NAMES = tuple(f'v3_w{wait}_h{history}'
                            for wait in (45, 60, 90) for history in (600, 900))
SELECTION_PATH = Path(__file__).resolve().parents[1] / 'config' / 'inquiry_v3_selected.json'


def _selected_variant():
    """정규 후보 하나만 선택한다. 실행 중 명세는 바뀌지 않도록 import 때 읽는다."""
    if not SELECTION_PATH.exists():
        return 'v3_w60_h900'
    try:
        selection = json.loads(SELECTION_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('invalid inquiry V3 selection manifest') from error
    if (not isinstance(selection, dict)
            or set(selection) != {'schema_version', 'source_policy', 'development_lock_sha256'}
            or type(selection['schema_version']) is not int or selection['schema_version'] != 1
            or selection['source_policy'] not in V3_EXPERIMENT_NAMES
            or not isinstance(selection['development_lock_sha256'], str)
            or re.fullmatch(r'[0-9a-fA-F]{64}', selection['development_lock_sha256']) is None):
        raise ValueError('invalid inquiry V3 selection manifest')
    return selection['source_policy']


SELECTED_VARIANT = _selected_variant()


def policy_specs(base_config):
    """기존 V2 설정을 수정하지 않고 별도의 V3 명세를 만든다."""
    def build(name, wait, history, internal):
        config = {**deepcopy(base_config), 'window_seconds': history,
                  'history_window_seconds': history, 'max_wait_seconds': wait}
        return dict(name=name,
                    label='관측 기반 V3' if not internal else f'V3 개발 후보 ({wait}초·이력 {history}초)',
                    version='1', batch_size=1, wait_seconds=wait, review_seconds=15,
                    max_contacts=10, deadline_seconds=300, experimental=True,
                    research_only=internal, config=config)

    specs = {}
    for wait in (45, 60, 90):
        for history in (600, 900):
            name = f'v3_w{wait}_h{history}'
            specs[name] = build(name, wait, history, True)
    selected = deepcopy(specs[SELECTED_VARIANT])
    selected.update(name='observed_v3', label='관측 기반 V3', research_only=False,
                    source_variant=SELECTED_VARIANT)
    specs['observed_v3'] = selected
    return specs


def bounded_wait(result, *, now, config, own, history_used_count,
                 history_excluded_count, max_new, last_contact_at=None):
    """최근 자신의 문의 이후 상한이 지나면 비용 순서의 다음 후보를 추가한다.

    own은 미래 시각을 제거한 자기 환자의 미해결 문의다. 180초가 지난
    문의는 정책 대기 수에서만 제외하며 호출자의 글로벌 부하에는 계속 남는다.
    last_contact_at은 호출자가 이미 보낸 마지막 문의의 시각이다. 닫힌 문의도
    포함하면 그 직전 문의를 기준으로 매 review마다 확대하는 일을 막는다.
    """
    output = deepcopy(result)
    waiting = [row for row in own
               if now - row['submitted_at'] <= config['observation_timeout_seconds']]
    latest = max((row['submitted_at'] for row in own), default=None)
    if last_contact_at is not None:
        latest = max(latest, last_contact_at) if latest is not None else last_contact_at
    age = now - latest if latest is not None else None
    evidence = output['evidence']
    evidence.update(history_window_seconds=config['history_window_seconds'],
                    history_used_count=history_used_count,
                    history_excluded_count=history_excluded_count,
                    max_wait_seconds=config['max_wait_seconds'],
                    last_contact_age_seconds=age,
                    bounded_wait_due=age is not None and age >= config['max_wait_seconds'])
    # V2 점수로 이미 고른 문의는 유지한다. 대기 상한은 수용 불가 판정이
    # 아니며, 정책상 동시 대기·전체 문의 수 제한을 넘어서는 이유도 아니다.
    if (not output['send'] and max_new > 0 and evidence['candidate_scores']
            and waiting and len(waiting) < config['max_concurrent']
            and age is not None and age >= config['max_wait_seconds']):
        output['send'] = [evidence['candidate_scores'][0]['hospital']]
        evidence['base_reason'] = output['reason']
        output['reason'] = 'bounded_wait_expansion'
        evidence['sent_hospitals'] = list(output['send'])
    return output
