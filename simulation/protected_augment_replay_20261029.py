"""G4 replay: unchanged opportunity constraints, admission-first search schedule.

Frozen G3 is preserved, including its failed large-batch regression. G4 uses
the same observed inputs and inquiry/transport model with a new search rule.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from . import final_assignment_engine_20261028 as controls
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import OnlineAllocationReplay, _ObservedCPSATBatch, SOURCE_EXPERIMENT
from .protected_augment_20261029 import ProtectedAugmentAssignmentRule
from .protected_replay_20261029 import EXECUTION_CONTRACT as G3_CONTRACT
from .protected_replay_audit_20261029 import audit_replay as audit_g3_constraints
from .sim import Policy, Simulation

EXPERIMENT_ID='protected_augment_20261029'
EXECUTION_CONTRACT=dict(G3_CONTRACT,search_schedule='admission_first_restart_then_regret_refinement',
    frozen_G3_policy_constraints_preserved=True,legacy_G3_results_overwritten=False)


class ProtectedAugmentReplay(OnlineAllocationReplay):
    def __init__(self,scenario,policy,routing=None):
        super().__init__(scenario,policy,routing=routing)
        self.protected_records=[]

    def resolve_batch(self,t):
        if not self.ready:
            return
        ready=list(self.ready);inputs=self._observed_input(ready,t);rule=self.policy.rule
        if rule.code!='G4':
            raise ValueError('G4 rule required')
        before=len(rule.records);offset=len(self.log);seconds=self.decision_seconds
        try:
            self.policy.rule=_ObservedCPSATBatch(rule,inputs)
            Simulation.resolve_batch(self,t)
        finally:
            self.policy.rule=rule
        actual={p.id:None for p,_ in ready}
        for event in self.log[offset:]:
            if event['type']=='commit' and event.get('patient') in actual:
                actual[event['patient']]=event['hospital']
        records=rule.records[before:]
        if len(records)!=1 or records[0]['actual_plan']!=actual:
            raise AssertionError('G4 search and actual commitments differ')
        record=deepcopy(records[0]);record['decision_index']=len(self.allocation_decisions)
        self.protected_records.append(record)
        self.allocation_decisions.append(dict(t=t,kind='batch',rule='G4',
            scope='current_same_minute_accepted_offers',inputs=inputs,assignments=actual,
            objective=self._objective(inputs,actual),method='protected_augmentation_restart',
            elapsed_seconds=self.decision_seconds-seconds,optimal_proven=False,
            protected_record_index=len(self.protected_records)-1))
        self.decision_batches.append(dict(t=t,patients=[p.id for p,_ in ready],
            method='protected_augmentation_restart',assignments=deepcopy(actual),
            scope='same_minute_ready_patients_only'))

    def run_replay(self):
        value=ReplaySimulation.run_replay(self)
        value.update(allocation_decisions=self.allocation_decisions,protected_records=self.protected_records,
            solver_records=[],source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID,
            execution_contract=deepcopy(EXECUTION_CONTRACT))
        value['metrics'].update(allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),protected_calls=len(self.protected_records),
            solver_calls=0,solver_wall_seconds=0,solver_fallbacks=0,whole_episode_optimal_proven=False)
        return value


def run_augmented_replay(scenario,routing=None):
    validate_scenario(scenario)
    original=deepcopy(scenario);fingerprint=controls.canonical_sha256(original)
    template,route_sha=controls._routing_template(routing)
    current=deepcopy(original)
    current.update(online_allocation_comparison=True,source_experiment=SOURCE_EXPERIMENT,
                   comparison_id=EXPERIMENT_ID)
    current.setdefault('assumptions',[]).extend([
        'G4 preserves current caller-order baseline admitted IDs; same-count travel cannot worsen any protected ID.',
        'Admission-first search restarts after an admission improvement before optimizing equivalent placements.',
        'The work budget is 2048 including partial search nodes; frozen G3 and old controls are unchanged.',
        'This policy can delay experimental priority-4 patients relative to G2; tradeoffs are reported.',
        'Current opportunity protection is not a whole-episode R1 or G2 nonregression or clinical guarantee.',
    ])
    isolated=RouteSnapshot(connector_speed_kmh=template.connector_speed_kmh)
    isolated.routes=deepcopy(template.routes)
    policy=Policy(rule=ProtectedAugmentAssignmentRule(),k=3,wait=2,notify=True,name='G4')
    replay=ProtectedAugmentReplay(deepcopy(current),policy,routing=isolated)
    branch=replay.run_replay();spec=policy.spec();spec['protected_assignment']=deepcopy(EXECUTION_CONTRACT)
    branch.update(id='G4',label='수용 기회 보호·증원 우선 재시작 G4',policy=spec,
                  original_scenario_sha256=fingerprint,routing_input_sha256=route_sha)
    if controls.canonical_sha256(scenario)!=fingerprint:
        raise AssertionError('caller input changed')
    return dict(schema_version=1,comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
        original_scenario=original,scenario=current,input_scenario_sha256=fingerprint,
        original_scenario_sha256=fingerprint,routing_input_sha256=route_sha,
        branches=[branch],execution_order=['G4'],routing_summary=dict(provider='synthetic',
            external_route_provider_allowed=False,live_traffic=False,shared_mutable_routing_cache=False,
            by_branch={'G4':isolated.summary(scenario_only=True)}))


def audit_augment_replay(replay,*,source_scenario=None):
    """Reuse frozen G3 constraint equations, not G3 search or outcomes.

    Labels are projected only in a deep copied audit view. The supplied G4 raw
    result, objective, decisions and endpoints remain unchanged and are checked.
    This does not rerun or relabel a G3 algorithm as a new performance result.
    """
    if replay.get('execution_order')!=['G4'] or len(replay.get('branches',[]))!=1:
        raise AssertionError('single actual G4 branch required')
    branch=replay['branches'][0]
    if branch['id']!='G4' or branch['policy']['rule']!='G4':
        raise AssertionError('G4 policy label differs')
    if branch['execution_contract']['search_schedule']!=EXECUTION_CONTRACT['search_schedule']:
        raise AssertionError('unexpected search schedule')
    projected=deepcopy(replay);projected['execution_order']=['G3']
    view=projected['branches'][0];view['id']='G3';view['policy']['rule']='G3'
    for decision in view['allocation_decisions']:
        if decision['rule']!='G4':
            raise AssertionError('unexpected G4 decision rule')
        decision['rule']='G3'
    result=audit_g3_constraints(projected,source_scenario=source_scenario)
    result['branch_metrics']={'G4':result['branch_metrics']['G3']}
    result['patient_vectors']={'G4':result['patient_vectors']['G3']}
    result.update(actual_algorithm='G4',constraint_equations='frozen_G3_opportunity_and_feasibility_only',
                  performance_algorithm_reused=False,original_G4_output_unchanged=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='G4 증원 우선 현재 수용 기회 보호 재생')
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv);target=Path(args.output)
    if target.exists():parser.error('기존 결과는 덮어쓰지 않습니다. 새 output 경로를 지정하세요.')
    try:
        source=json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        replay=run_augmented_replay(source);audit=audit_augment_replay(replay,source_scenario=source)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf-8') as stream:
            json.dump(dict(synthetic=True,operational_adoption=False,replay=replay,audit=audit),
                      stream,ensure_ascii=False,indent=2)
    except (OSError,ValueError) as error:parser.error(str(error))
    print(json.dumps(dict(passed=audit['passed'],rule='G4',output=str(target.resolve())),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
