"""실제 서버 문의 워크플로 실험기에 EAT·V4를 실행 중에만 등록해 같은 조건으로 비교한다.

`server/` 파일은 수정하지 않는다. 이 프로세스 안에서만 정책 명세와 decide를 덧붙인다.
결과는 환자별 선정·예상 도착·문의 수와 병원 검토 업무량만 압축해 남긴다.
"""
from server import inquiry_experiment, inquiry_policies, inquiry_v4

from . import eat_policy, scenarios

BASELINES = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2', 'v3_w45_h900', 'observed_v4')
FAILURE_PENALTY_SECONDS = 1500   # eat_policy와 같은 실패 비용. 민감도는 600·3000초로 따로 보고
_ORIGINAL_DECIDE = inquiry_policies.decide
_INSTALLED = set()


def install(names=()):
    """EAT 후보와 V4를 이 프로세스의 정책 레지스트리에 등록한다(디스크 변경 없음)."""
    if 'observed_v4' not in inquiry_policies._POLICIES:
        inquiry_policies._POLICIES['observed_v4'] = inquiry_v4.spec()
    for name in names:
        if name not in inquiry_policies._POLICIES:
            inquiry_policies._POLICIES[name] = eat_policy.spec(name)
        _INSTALLED.add(name)
    if inquiry_policies.decide is _ORIGINAL_DECIDE:
        def decide(name, **kwargs):
            if name.startswith(eat_policy.POLICY_PREFIX):
                return eat_policy.decide(name, **kwargs)
            if name == 'observed_v4':
                return inquiry_v4.decide(**kwargs)
            return _ORIGINAL_DECIDE(name, **kwargs)
        inquiry_policies.decide = decide


def uninstall():
    """같은 프로세스의 다른 테스트를 위해 원래 레지스트리와 decide로 되돌린다."""
    inquiry_policies.decide = _ORIGINAL_DECIDE
    for name in list(_INSTALLED) + ['observed_v4']:
        inquiry_policies._POLICIES.pop(name, None)
    _INSTALLED.clear()


def build(kind, seed):
    if kind == 'mix':
        return scenarios.make_mix(seed)
    family, _, raw = kind.partition(':')
    if family != 'family':
        raise ValueError('kind must be mix or family:<name>')
    suite = scenarios.make_family_suite((seed,))
    return next(s for s in suite if s['family'] == raw)


def patient_cost(row, penalty=FAILURE_PENALTY_SECONDS):
    """선정 실패는 관측 300초 + 벌점. 성공은 예상 도착(선정 경과 + 이동시간)."""
    return 300 + penalty if row['failed'] else row['estimated_arrival_seconds']


def compact(branch):
    reasons = {}
    for d in branch['decisions']:
        reasons[d['reason']] = reasons.get(d['reason'], 0) + 1
    m = branch['metrics']
    return dict(policy=branch['policy'],
                patients=[dict(id=p['id'], created_at=p['created_at'], failed=p['failed'],
                               selection_seconds=p['selection_seconds'],
                               estimated_arrival_seconds=p['estimated_arrival_seconds'],
                               travel_seconds=p['travel_seconds'],
                               destination=p['destination_hospital_id'],
                               inquiry_count=p['inquiry_count'],
                               contacted=p['contacted_hospitals'])
                          for p in branch['patients']],
                review_work_seconds=m['review_work_seconds'],
                review_started_count=m['review_started_count'],
                cancelled_before_start_count=m['cancelled_before_start_count'],
                reply_suppressed_count=m['reply_suppressed_count'],
                decision_reasons=reasons)


def run_case(task):
    """(kind, seed, policies) 하나를 실행한다. 프로세스 풀의 작업 단위."""
    kind, seed, policies = task
    install([p for p in policies if p.startswith(eat_policy.POLICY_PREFIX)])
    scenario = build(kind, seed)
    result = inquiry_experiment.run_scenario(scenario, policies=tuple(policies))
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']),
                oracle=scenarios.oracle(scenario),
                branches={b['policy']: compact(b) for b in result['branches']})
