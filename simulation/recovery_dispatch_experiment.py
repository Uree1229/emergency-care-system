"""Frozen recovery wake experiment with exact saved development references.

The preceding 441xx inputs are explicitly reused development data. Only new
candidate branches run on those inputs. New boundaries and fresh validation
execute all ten declared references. Neither old reference work nor CPU times
are rewritten or counted as new execution.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from . import retry_dispatch_experiment as previous
from .recovery_dispatch_engine import run_recovery_dispatch_branch
from .recovery_dispatch_policy import VARIANTS
from .recovery_dispatch_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES,
    DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario,
)

ROOT = previous.ROOT
BUILD = ROOT / '.codex-build/recovery-dispatch-20261018'
EXPERIMENT_ID = 'recovery_dispatch_20261018'
PARENT_BUILD = ROOT / '.codex-build/retry-dispatch-20261018'
PARENT_REPORT = ROOT / 'simulation_results/retry_dispatch_development_20261018.json'
NEW_SOURCES = tuple('simulation/recovery_dispatch_' + name + '.py' for name in
    ('policy', 'engine', 'audit', 'scenarios', 'experiment')) + tuple(
    'test_recovery_dispatch_' + name + '.py' for name in
    ('policy', 'engine', 'audit', 'scenarios', 'experiment'))
SOURCE_FILES = tuple(sorted(set(previous.SOURCE_FILES) | set(NEW_SOURCES)))
AGGREGATE_FIELDS = previous.AGGREGATE_FIELDS
PARENT_SOURCE_COUNT = len(previous.SOURCE_FILES)


def raw(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(value)


def candidate_id(allocation, variant):
    if allocation not in ('H1', 'C1') or variant not in VARIANTS:
        raise ValueError('known recovery allocation and variant required')
    return f'recovery_{allocation}_{variant}'


def fresh_references():
    wanted = {f'v4_{a}_{stage}' for a in ('H1', 'C1') for stage in ('safe', 'probe', 'baseline')}
    wanted |= {f'fixed3_{a}_baseline' for a in ('H1', 'C1')}
    rows = [deepcopy(p) for p in previous.REFERENCES if p['id'] in wanted]
    rows += [dict(id=f'retry_{a}_frontier', allocation=a, variant='frontier') for a in ('H1', 'C1')]
    if len(rows) != 10:
        raise ValueError('the ten predeclared fresh references differ')
    return rows


def cached_references():
    return deepcopy(list(previous.REFERENCES)) + [
        dict(id=f'retry_{a}_frontier', allocation=a, variant='frontier') for a in ('H1', 'C1')]


def policies_for(phase, selection=None, *, cached=False):
    if phase not in ('development', 'validation'):
        raise ValueError('known phase required')
    if phase == 'validation' and cached:
        raise ValueError('validation references must all execute on fresh inputs')
    if phase == 'validation' and (not isinstance(selection, dict) or set(selection) != {'H1', 'C1'}):
        raise ValueError('validation requires fixed development choices')
    refs = cached_references() if cached else fresh_references()
    return refs + [dict(id=candidate_id(a, v), allocation=a, variant=v, recovery_dispatch=True)
        for a in ('H1', 'C1') for v in (VARIANTS if phase == 'development' else (selection[a]['variant'],))]


def pairs_for(phase, selection=None):
    pairs = []
    for allocation in ('H1', 'C1'):
        variants = VARIANTS if phase == 'development' else (selection[allocation]['variant'],)
        for variant in variants:
            for tag, before in (('current', f'retry_{allocation}_frontier'),
                ('safe', f'v4_{allocation}_safe'), ('prototype', f'v4_{allocation}_probe'),
                ('protected', f'v4_{allocation}_baseline'), ('fixed3_reference', f'fixed3_{allocation}_baseline')):
                pairs.append((f'{variant}_{tag}_{allocation}', before, candidate_id(allocation, variant)))
    return tuple(pairs)


SELECTION_RULE = dict(
    population='development_only', primary_reference='frozen frontier on the same input',
    no_lost_treatments=True, no_individual_treatment_or_final_arrival_worsening=True,
    no_case_total_pure_wait_or_capped_treatment_increase=True,
    no_case_inquiry_increase=True, maximum_primary_total_inquiry_ratio=1.0,
    require_strict_total_pure_wait_reduction=True,
    require_strict_capped_treatment_reduction=True,
    strict_total_inquiry_reduction_required=False,
    inquiry_objective='preserve preceding frontier inquiry gains while repairing recovery latency',
    inherited_individual_reference='safe', inherited_population_references=['probe', 'baseline'],
    inherited_maximum_total_inquiry_ratio=1.10,
    fixed3_scope='reference only; original uncapped episode and different foundation',
    ranking=['primary_passes', 'lost_treatments', 'individual_clinical_worsenings',
             'case_wait_increases', 'capped_treatment', 'total_pure_wait', 'inquiries', 'variant_order'],
    no_primary_pass='preserve failed development evidence and skip reserved validation',
    live_default='fixed_3', adopted=False)
SETTINGS = dict(
    variants=list(VARIANTS), clinical_priority_and_physical_inputs_unchanged=True,
    existing_resource_channels_only=True, added_resource_change_publications=False,
    patient_open_limit=3, patient_episode_contact_limit=10,
    original_actual_contact_clock_timeouts_offer_ttl_fifo_and_workers=True,
    cache_population='the preceding 109 families and 441xx development seeds only',
    cached_references='old twelve plus development-selected frontier H1/C1; original full raw and proof',
    fresh_references='frontier/safe/probe/protected/fixed3, each H1 and C1',
    omitted_from_fresh_benchmark=['capability', 'cooperative'],
    omitted_scope='their complete earlier results remain preserved; no new superiority claim against them',
    cached_cpu_time_scope='different execution occasions; not a policy gate or causal latency comparison',
    original_cached_execution_order_preserved_separately=True,
    cached_branches_not_counted_as_new_runs_or_new_audits=True,
    validation_not_used_for_tuning=True, isolated_simulation=True,
    live_server_ui_database_or_default_modified=False,
    selection_rule=deepcopy(SELECTION_RULE))


def parent_binding():
    proof_path = PARENT_BUILD / 'verification.json'
    proof = read(proof_path)
    dev_audit_path = PARENT_BUILD / 'development-independent-audit.json'
    dev_audit = read(dev_audit_path)
    data = PARENT_REPORT.read_bytes()
    if not proof['passed'] or not dev_audit['passed'] or dev_audit['report_sha256'] != sha(data):
        raise ValueError('completed parent development proof is required')
    preservation = read(BUILD / 'preservation-before.json')
    if not preservation['passed'] or preservation['parent_verification_sha256'] != sha(proof_path.read_bytes()):
        raise ValueError('parent final proof differs from the new preservation baseline')
    for path in (PARENT_REPORT, dev_audit_path):
        name = path.relative_to(ROOT).as_posix()
        if proof['final_output_sha256'].get(name) != sha(path.read_bytes()):
            raise ValueError('the parent final proof does not bind the cached report and complete audit')
    if len(proof['source_sha256']) != 146 or any(
            sha((ROOT / name).read_bytes()) != digest for name, digest in proof['source_sha256'].items()):
        raise ValueError('one of the 146 frozen parent sources changed')
    return dict(verification=str(proof_path), verification_sha256=sha(proof_path.read_bytes()),
        development_report=str(PARENT_REPORT), development_report_sha256=sha(data),
        development_independent_audit=str(dev_audit_path),
        development_independent_audit_sha256=sha(dev_audit_path.read_bytes()),
        frozen_parent_sources=proof['source_sha256'])


def protocol():
    return dict(experiment_id=EXPERIMENT_ID, families=list(FAMILIES),
        inherited_families=list(ORIGINAL_FAMILIES), additional_families=list(ADDITIONAL_FAMILIES),
        development_seeds=list(DEVELOPMENT_SEEDS), validation_seeds=list(VALIDATION_SEEDS),
        development_seeds_previously_observed=True, settings=deepcopy(SETTINGS),
        source_sha256={name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES},
        parent=parent_binding(), runtime_versions=previous.runtime_versions())


class SavedDevelopmentReferences:
    """Read exact complete parent objects; never project/relabel an audit branch."""
    def __init__(self):
        self.binding = parent_binding()
        self.report = read(PARENT_REPORT)
        self.by_case = {(c['seed'], c['family']): c for c in self.report['cases']}
        self.ids = [p['id'] for p in cached_references()]
        if len(self.ids) != 14 or any(self.report['selected_policy'][a]['variant'] != 'frontier' for a in ('H1', 'C1')):
            raise ValueError('the exact frozen frontier choices are required')

    def load(self, scenario):
        key = (scenario['seed'], scenario['family'])
        case = self.by_case.get(key)
        if case is None:
            raise ValueError('this development input has no frozen cached references')
        path = Path(self.report['artifact_directory']) / case['file']
        zipped = path.read_bytes()
        if sha(zipped) != case['compressed_sha256']:
            raise ValueError('cached compressed raw changed')
        content = gzip.decompress(zipped)
        replay = json.loads(content)
        if sha(content) != case['raw_sha256'] or replay['scenario'] != scenario \
                or sha(raw(scenario)) != case['original_scenario_sha256']:
            raise ValueError('cached full raw or original input differs')
        by_id = {b['id']: b for b in replay['branches']}
        branches = {code: by_id[code] for code in self.ids}
        for code, branch in branches.items():
            if previous.summarize_branch(scenario, branch) != case['summaries'][code] \
                    or not case['audits'][code]['passed']:
                raise ValueError('cached raw summary or original complete audit differs')
        provenance = dict(**self.binding, source_case_file=case['file'],
            source_compressed_sha256=case['compressed_sha256'], source_raw_sha256=case['raw_sha256'],
            source_original_scenario_sha256=case['original_scenario_sha256'],
            source_execution_order=deepcopy(case['execution_order']),
            source_canonical_branch_ids=[b['id'] for b in replay['branches']],
            source_policy_rows=[deepcopy(p) for p in self.report['policies'] if p['id'] in self.ids],
            cached_branch_ids=list(self.ids),
            cached_branch_sha256={code: sha(raw(branch)) for code, branch in branches.items()},
            cached_summary_sha256={code: sha(raw(case['summaries'][code])) for code in self.ids},
            cached_audit_sha256={code: sha(raw(case['audits'][code])) for code in self.ids},
            proof_reused_without_reexecution=True, new_execution_count=0, new_audit_count=0)
        return branches, deepcopy({code: case['audits'][code] for code in self.ids}), provenance


def run_policy(scenario, policy):
    if policy.get('recovery_dispatch'):
        return run_recovery_dispatch_branch(scenario, allocation=policy['allocation'], variant=policy['variant'],
            branch_id=policy['id'], label=policy['id'])
    if policy['id'].startswith('retry_'):
        return previous.run_retry_dispatch_branch(scenario, allocation=policy['allocation'], variant='frontier',
            branch_id=policy['id'], label=policy['id'])
    return previous.old.run_capability_probe_replay(scenario, policies=[deepcopy(policy)],
        execution_order=[policy['id']])['branches'][0]


def totals(cases, policy_ids):
    return {code: dict(cases=len(rows), **{field: sum(row[field] for row in rows) for field in AGGREGATE_FIELDS})
        for code in policy_ids if (rows := [case['summaries'][code] for case in cases if code in case['summaries']])}


def comparisons(cases, pairs):
    return previous.combined_pairs(cases, pairs)


def assess(cases, allocation, variant):
    after = candidate_id(allocation, variant)
    def check(before, primary=False):
        changes = [previous.compare(c['summaries'][before], c['summaries'][after]) for c in cases]
        inquiry_before = sum(c['summaries'][before]['inquiries'] for c in cases)
        inquiry_after = sum(c['summaries'][after]['inquiries'] for c in cases)
        out = dict(no_lost_treatments=all(c['lost_treatments'] == 0 for c in changes),
            no_individual_treatment_worsening=all(c['worsened'] == 0 for c in changes),
            no_individual_final_arrival_worsening=all(c['final_arrival_worsened'] == 0 for c in changes),
            no_case_total_wait_increase=all(c['total_wait_delta_minutes'] <= 0 for c in changes),
            no_case_capped_treatment_increase=all(c['capped_treatment_delta_minutes'] <= 0 for c in changes),
            bounded_inquiry_cost=inquiry_after <= inquiry_before * (1.0 if primary else 1.10),
            strict_total_wait_reduction=sum(c['total_wait_delta_minutes'] for c in changes) < 0)
        if primary:
            out.update(no_case_inquiry_increase=all(c['inquiry_delta'] <= 0 for c in changes),
                strict_capped_treatment_reduction=sum(c['capped_treatment_delta_minutes'] for c in changes) < 0)
        return out
    primary = check(f'retry_{allocation}_frontier', True)
    inherited = {name: check(f'v4_{allocation}_{stage}') for name, stage in
        (('safe', 'safe'), ('prototype', 'probe'), ('protected', 'baseline'))}
    population = ('no_lost_treatments', 'no_case_total_wait_increase', 'no_case_capped_treatment_increase',
        'bounded_inquiry_cost', 'strict_total_wait_reduction')
    gates = dict(vs_current=all(primary.values()), vs_safe=all(inherited['safe'].values()),
        vs_prototype_population=all(inherited['prototype'][key] for key in population),
        vs_protected_population=all(inherited['protected'][key] for key in population))
    differences = [previous.compare(c['summaries'][f'retry_{allocation}_frontier'], c['summaries'][after]) for c in cases]
    ranking = [not gates['vs_current'], sum(c['lost_treatments'] for c in differences),
        sum(c['worsened'] + c['final_arrival_worsened'] for c in differences),
        sum(c['total_wait_delta_minutes'] > 0 for c in differences),
        sum(c['summaries'][after]['request_to_treatment_capped_sum_minutes'] for c in cases),
        sum(c['summaries'][after]['total_wait_sum_minutes'] for c in cases),
        sum(c['summaries'][after]['inquiries'] for c in cases), list(VARIANTS).index(variant)]
    return dict(variant=variant, policy=after, primary_checks=primary, inherited_checks=inherited,
        gates=gates, primary_passed=gates['vs_current'], deployment_eligible=all(gates.values()), ranking=ranking)


def select_policy(cases):
    chosen = {}
    for allocation in ('H1', 'C1'):
        candidates = [assess(cases, allocation, variant) for variant in VARIANTS]
        result = min(candidates, key=lambda row: row['ranking'])
        chosen[allocation] = dict(**result, all_candidates=candidates,
            selection_scope='development research candidate only', live_default='fixed_3', adopted=False)
    return chosen


def verify_freeze(path=None):
    path = Path(path) if path is not None else BUILD / 'pre-development-freeze.json'
    if not path.is_file():
        raise ValueError('source/input/seed freeze is required before reserved execution')
    frozen = read(path)
    if not frozen['passed'] or frozen['frozen_protocol'] != protocol():
        raise ValueError('all sources/settings/parent proof must be frozen before execution')
    mandatory = {'preservation-before.json', 'tests.json', 'seed-audit-history.json',
        'seed-audit-current-coverage.json', 'smoke.json', 'source-freeze.json',
        'scenario-input-manifest.json', 'audit_artifacts.py'}
    if not mandatory <= set(frozen.get('evidence_sha256', {})):
        raise ValueError('complete frozen preservation/test/seed/input/audit prerequisites are required')
    for name, digest in frozen['evidence_sha256'].items():
        target = (BUILD / name).resolve()
        if not target.is_relative_to(BUILD.resolve()) or sha(target.read_bytes()) != digest:
            raise ValueError('frozen prerequisite evidence changed: ' + name)
    return frozen


def verify_lock(lock):
    if lock['protocol'] != protocol():
        raise ValueError('frozen source/settings differ from development lock')
    data = Path(lock['development_report']).read_bytes()
    if sha(data) != lock['development_report_sha256']:
        raise ValueError('locked development report changed')
    report = json.loads(data)
    if select_policy(report['cases']) != lock['selected_policy']:
        raise ValueError('development-only selection no longer reproduces')
    if not lock['validation_allowed']:
        raise ValueError('no primary-passing development candidate; reserved validation is intentionally not run')


def markdown(report):
    lines = ['# 관측 회복을 기억하는 재문의 실험', '',
        '합성 실험. 441xx는 이미 관측한 개발 입력이며 저장 참조를 재사용했다. 461xx만 새 검증이다.',
        '고정 3곳은 회차 10회 한도가 없는 전체 스택 참고 기준이다. 기본 fixed_3는 변경하지 않았다.', '',
        f"환경 {len(report['cases'])}, 새 실행/새 완전 원문 감사 {report['runs']}, 저장 참조 증명 재사용 {report['cached_reference_branches']}", '',
        '| 분기 | 환경 | 환자 | 미처치 | 순대기 합 | 제한 처치 합 | 실제 문의 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for code, row in report['totals'].items():
        lines.append(f"| {code} | {row['cases']} | {row['patients']} | {row['untreated']} | {row['total_wait_sum_minutes']:.2f} | {row['request_to_treatment_capped_sum_minutes']:.2f} | {row['inquiries']} |")
    lines += ['', '| 비교 | 처치 지표 개선 | 지표 악화 | 성공 상실 | 새 성공 | 최대 제한 악화 | 순대기 변화 | 대기 증가 환경 | 문의 변화 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for code, row in report['comparisons'].items():
        lines.append(f"| {code} | {row['improved']} | {row['worsened']} | {row['lost_treatments']} | {row['newly_treated']} | {row['maximum_treatment_worsening_minutes']:.2f} | {row['total_wait_delta_minutes']:+.2f} | {row['cases_with_longer_total_wait']} | {row['inquiry_delta']:+} |")
    lines += ['', '개발 고정 연구 선택과 검증 진단:', '', '```json', json.dumps(dict(
        selected=report['selected_policy'], validation=report['validation_diagnostics']), ensure_ascii=False, indent=2), '```', '',
        '순대기는 이동을 제외한 탐색·병원·전원 준비 대기다. 미처치는 동일 관찰 종료로 제한한다.',
        '개인 순대기는 별도 진단하며 개인 임상 도착·처치와 환경별 순대기 gate를 구분한다.',
        'cached CPU와 새 CPU는 다른 실행 시점이다. 50ms fallback을 재실행·제외하지 않고 숨기지 않는다.', '']
    return '\n'.join(lines)


def execute(phase, output, lock_path):
    from .recovery_dispatch_audit import audit_branch
    output, lock_path = Path(output), Path(lock_path)
    report_path, folder = output.with_suffix('.json'), output.parent / (output.name + '_cases')
    if phase not in ('development', 'validation'):
        raise ValueError('known phase required')
    if any(path.exists() for path in (report_path, output.with_suffix('.md'), folder)):
        raise ValueError('saved scientific evidence cannot be overwritten')
    if phase == 'development' and lock_path.exists():
        raise ValueError('development selection is already locked')
    verify_freeze()
    lock = read(lock_path) if phase == 'validation' else None
    if lock:
        verify_lock(lock)
        development_audit = read(BUILD / 'development-independent-audit.json')
        if development_audit.get('passed') is not True \
                or development_audit.get('report_sha256') != lock['development_report_sha256']:
            raise ValueError('a complete independent audit of locked development is required before validation')
    frozen = protocol()
    cache = SavedDevelopmentReferences() if phase == 'development' else None
    selection = None if lock is None else lock['selected_policy']
    seeds = DEVELOPMENT_SEEDS if phase == 'development' else VALIDATION_SEEDS
    pairs = pairs_for(phase, selection)
    folder.mkdir(parents=True)
    write_new(folder / 'protocol.json', raw(frozen))
    expected_inputs = {(row['seed'], row['family']): row['sha256'] for row in
        read(BUILD / 'scenario-input-manifest.json')['entries']}
    cases, executed, reused = [], 0, 0
    for si, seed in enumerate(seeds):
        for fi, family in enumerate(FAMILIES):
            scenario = make_scenario(seed, family)
            if sha(raw(scenario)) != expected_inputs[seed, family]:
                raise ValueError('scenario differs from the pre-development input manifest')
            cached = phase == 'development' and family in ORIGINAL_FAMILIES
            policies = policies_for(phase, selection, cached=cached)
            ids = [p['id'] for p in policies]
            branches, audits, provenance = cache.load(scenario) if cached else ({}, {}, None)
            new_policies = [p for p in policies if p['id'] not in branches]
            fresh_ids = [p['id'] for p in new_policies]
            offset = (si + fi) % len(fresh_ids)
            order = fresh_ids[offset:] + fresh_ids[:offset]
            by_id = {p['id']: p for p in new_policies}
            fresh_branches = {}
            for code in order:
                fresh_branches[code] = run_policy(scenario, by_id[code])
            # Save full newly executed objects before accepting an audit result.
            replay = dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=deepcopy(scenario),
                branches=[fresh_branches[code] for code in fresh_ids], execution_order=order,
                cached_reference_provenance=provenance,
                cached_reference_execution_order=[] if provenance is None else provenance['source_execution_order'])
            content = raw(replay)
            zipped = gzip.compress(content, compresslevel=6, mtime=0)
            name = f'{seed}_{fi:03d}_{family}.json.gz'
            write_new(folder / name, zipped)
            for code in fresh_ids:
                branch = fresh_branches[code]
                audits[code] = audit_branch(scenario, branch)
                if audits[code]['passed'] is not True:
                    raise ValueError('new full raw audit failed')
                branches[code] = branch
            summaries = {code: previous.summarize_branch(scenario, branches[code]) for code in ids}
            if any(branch.get('resource_updates_enabled') is not False for branch in branches.values()) \
                    or any(row[key] for row in summaries.values() for key in
                        ('added_resource_publications', 'added_resource_update_receipts', 'added_resource_update_cycles')):
                raise ValueError('an additional resource-change report channel appeared')
            executed += len(fresh_ids)
            reused += len(ids) - len(fresh_ids)
            cases.append(dict(seed=seed, family=family, file=name,
                source_kind='saved_development_references' if cached else 'all_fresh',
                compressed_sha256=sha(zipped), raw_sha256=sha(content), original_scenario_sha256=sha(raw(scenario)),
                branch_ids=ids, freshly_executed_branch_ids=fresh_ids, actual_execution_order=order,
                reused_reference_branch_ids=[] if provenance is None else provenance['cached_branch_ids'],
                cached_reference_provenance=provenance, summaries=summaries, audits=audits,
                audit_provenance={code: ('reused_parent_complete_independent_proof' if code not in fresh_ids
                    else 'new_full_raw_online_audit') for code in ids},
                comparisons={tag: previous.compare(summaries[a], summaries[b]) for tag, a, b in pairs}))
            print(f'{phase} {len(cases)}/{len(seeds)*len(FAMILIES)} new={executed} cached={reused} {seed} {family}', flush=True)
    if protocol() != frozen:
        raise ValueError('scientific source/settings changed during execution')
    selected = select_policy(cases) if lock is None else lock['selected_policy']
    diagnostics = None if lock is None else {a: assess(cases, a, selected[a]['variant']) for a in ('H1', 'C1')}
    universe = [p['id'] for p in policies_for(phase, selection, cached=phase == 'development')]
    report = dict(schema_version=1, experiment_id=EXPERIMENT_ID, synthetic=True, phase=phase,
        protocol=frozen, artifact_directory=str(folder.resolve()), cases=cases, runs=executed,
        cached_reference_branches=reused, total_compared_branches=executed+reused,
        cached_branch_proofs_counted_as_new_audits=False, original_cached_execution_order_preserved=True,
        fresh_reference_policies=fresh_references(), cached_reference_policies=cached_references(),
        candidate_policies=[p for p in policies_for(phase, selection) if p.get('recovery_dispatch')],
        pairs=[list(p) for p in pairs], totals=totals(cases, universe), comparisons=comparisons(cases, pairs),
        selected_policy=selected, validation_diagnostics=diagnostics,
        validation_allowed=all(row['primary_passed'] for row in selected.values()))
    report['families'] = [dict(family=family, cases=len(group), totals=totals(group, universe),
        comparisons=comparisons(group, pairs)) for family in FAMILIES
        if (group := [case for case in cases if case['family'] == family])]
    data = raw(report)
    write_new(report_path, data)
    write_new(output.with_suffix('.md'), markdown(report).encode('utf-8'))
    if lock is None:
        write_new(lock_path, raw(dict(protocol=frozen, development_report=str(report_path.resolve()),
            development_report_sha256=sha(data), selected_policy=selected,
            validation_allowed=report['validation_allowed'])))
    else:
        verify_lock(lock)
    print(json.dumps(dict(output=str(report_path), cases=len(cases), new_runs=executed,
        cached_reference_branches=reused, validation_allowed=report['validation_allowed'],
        selection={a: dict(variant=row['variant'], primary_passed=row['primary_passed'],
            deployment_eligible=row['deployment_eligible']) for a, row in selected.items()}), ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('development', 'validation'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lock', type=Path, required=True)
    args = parser.parse_args()
    execute(args.phase, args.output, args.lock)


if __name__ == '__main__':
    main()
