"""Physical-loop AST invariants and tiny synthetic namespace integration only."""
from copy import deepcopy
import ast
import inspect
import os
import json
import hashlib
from pathlib import Path
import unittest
from server import inquiry_policies,inquiry_workflow
from inquiry_lab import runner as old,horizon_runner,cohort_tailwait_runner as accepted
from inquiry_lab import cohort_chainplanwake_execution as e,cohort_chainplanwake_runner as r


def scenario(two=False,decline=False):
    hospitals=[dict(id='H1',name='H1',lat=35.1,lon=127.1,capabilities=['응급의학'],workers=1)]
    if decline:hospitals.append(dict(id='H2',name='H2',lat=35.2,lon=127.2,capabilities=['응급의학'],workers=1))
    patients=[dict(id='P'+str(i),at=0,lat=35.+i*.001,lon=127.,required_capabilities=[],travel_seconds={h['id']:100+j for j,h in enumerate(hospitals)}) for i in range(2 if two else 1)]
    potentials=[dict(patient_id=p['id'],hospital_id=h['id'],accept=not (decline and h['id']=='H1'),no_response=False,
        service_seconds=2 if h['id']=='H1' else 5) for p in patients for h in hospitals]
    return dict(id='tiny-namespace',hospitals=hospitals,patients=patients,potentials=potentials,history=[],horizon_seconds=300)


class ChainplanwakeExecutionTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[2]/'.codex-build/eat-chainplanwake-20261028/tmp';root.mkdir(parents=True,exist_ok=True)
        self.oldenv={k:os.environ.get(k) for k in ('TEMP','TMP')}
        for key in self.oldenv:os.environ[key]=str(root)

    def tearDown(self):
        for key,value in self.oldenv.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value

    def test_physical_branch_AST_changes_only_explicit_workflow_and_policy_lookup(self):
        oldtree=ast.parse(inspect.getsource(old._branch));newtree=ast.parse(inspect.getsource(e._branch))
        class Expected(ast.NodeTransformer):
            def visit_Call(self,node):
                self.generic_visit(node)
                if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name):
                    if node.func.value.id=='inquiry_workflow' and node.func.attr=='run_once':node.func=ast.Name(id='workflow_step',ctx=ast.Load())
                    elif node.func.value.id=='inquiry_policies' and node.func.attr=='spec':node.func.value.id='candidate_policy'
                return node
        oldtree=Expected().visit(oldtree)
        newtree.body[0].args.kwonlyargs=[];newtree.body[0].args.kw_defaults=[]
        self.assertEqual(ast.dump(oldtree,include_attributes=False),ast.dump(newtree,include_attributes=False))

    def test_tiny_execution_leaves_original_functions_registry_and_horizon_namespace_unchanged(self):
        from server import service as old_service
        from inquiry_lab import harness
        modules=(inquiry_workflow,inquiry_policies,horizon_runner,old_service,harness)
        def bindings():return {module.__name__:{key:id(value) for key,value in vars(module).items()} for module in modules}
        bindings_before=bindings();installed_before=set(harness._INSTALLED)
        functions=(inquiry_workflow.run_once,inquiry_workflow._capture,inquiry_workflow._apply,inquiry_policies.decide,
            horizon_runner.horizon_policy,horizon_runner._capture_time_context)
        registry=deepcopy(inquiry_policies._POLICIES)
        result=r.run_scenario(scenario(),[(r.NAME,'immediate')])
        self.assertEqual(result['branches'][0]['patients'][0]['selection_seconds'],2)
        self.assertEqual(functions,(inquiry_workflow.run_once,inquiry_workflow._capture,inquiry_workflow._apply,inquiry_policies.decide,
            horizon_runner.horizon_policy,horizon_runner._capture_time_context))
        self.assertEqual(inquiry_policies._POLICIES,registry);self.assertNotIn(r.NAME,inquiry_policies._POLICIES)
        bindings_after=bindings();self.assertEqual(bindings_before,bindings_after);self.assertEqual(set(harness._INSTALLED),installed_before)
        workspace=Path(__file__).resolve().parents[2];folder=workspace/'.codex-build/eat-chainplanwake-20261028/diagnosis';folder.mkdir(parents=True,exist_ok=True)
        proof=dict(passed=True,synthetic_only=True,scope='All namespace binding names/object identities for five original modules plus policy registry contents and installed-policy set; not arbitrary recursive object state.',
            original_namespace_bindings_before=bindings_before,original_namespace_bindings_after=bindings_after,
            original_policy_registry_before_sha256=hashlib.sha256(json.dumps(registry,sort_keys=True,allow_nan=False).encode()).hexdigest(),
            original_policy_registry_after_sha256=hashlib.sha256(json.dumps(inquiry_policies._POLICIES,sort_keys=True,allow_nan=False).encode()).hexdigest(),
            original_harness_installed_before=sorted(installed_before),original_harness_installed_after=sorted(harness._INSTALLED),
            original_module_source_sha256={module.__name__:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in modules},
            new_source_sha256={('inquiry_lab/cohort_chainplanwake_'+part+'.py'):hashlib.sha256((workspace/'inquiry_lab'/('cohort_chainplanwake_'+part+'.py')).read_bytes()).hexdigest()
                for part in ('policy','context','workflow','service','execution','runner')},
            production_scenario_runs=0,tiny_synthetic_branch_runs_in_this_assertion=1,actual_benefit_validated=False)
        for index in range(1,10000):
            path=folder/f'namespace-proof-{index:03d}.json'
            if not path.exists():
                with path.open('x',encoding='utf-8') as stream:json.dump(proof,stream,sort_keys=True,allow_nan=False)
                break

    def test_public_decline_wake_occurs_in_tiny_physical_loop_without_private_policy_inputs(self):
        result=r.run_scenario(scenario(decline=True),[(r.NAME,'immediate')]);branch=result['branches'][0]
        self.assertEqual(branch['patients'][0]['selection_seconds'],5)
        wakes=[call for call in branch['policy_calls'] if call['output']['evidence']['cohort_chainplanwake']['triggered']]
        self.assertEqual(len(wakes),1);self.assertEqual(wakes[0]['at'],2)
        self.assertEqual(wakes[0]['output']['next_review_at'],1800000015)
        def private(value):
            if isinstance(value,dict):return bool(set(value)&{'potentials','workers','service_seconds','no_response'}) or any(private(x) for x in value.values())
            if isinstance(value,list):return any(private(x) for x in value)
            return False
        self.assertFalse(any(private(call['inputs']) for call in branch['policy_calls']))

    def test_no_decline_two_patient_clinical_outcomes_and_FIFO_trace_equal_accepted_Tailwait(self):
        world=scenario(two=True)
        candidate=r.run_scenario(world,[(r.NAME,'immediate')])['branches'][0]
        baseline=accepted.run_scenario(world,[(accepted.NAME,'immediate')])['branches'][0]
        self.assertEqual(candidate['patients'],baseline['patients']);self.assertEqual(candidate['metrics'],baseline['metrics'])
        self.assertEqual(candidate['trace'],baseline['trace'])


if __name__=='__main__':unittest.main()
