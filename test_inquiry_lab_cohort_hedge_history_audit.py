"""Public history provenance regressions; no policy, scenario or model runs."""
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_hedge_history_audit as audit

F=audit.frozen
ROOT=Path(__file__).resolve().parent


def fixture(action='decline',answered=True):
    now=audit.EPOCH;pid='old-public-alpha';hids=['H01','H02','H03']
    current=dict(id='P01',at=0,lat=35.,lon=127.,travel_seconds=dict(H01=100,H02=120,H03=140))
    history=dict(patient_id=pid,hospital_id='H02',sent_at=-100,reply_at=-50 if answered else None,action=action)
    scenario=dict(patients=[current],history=[history])
    events=[dict(id=1,at=now-101,type='patient_created',patient_id=pid,hospital_id=None,data={'danger':False}),
        dict(id=2,at=now-100,type='inquiry_sent',patient_id=pid,hospital_id='H02',data={'inquiry_id':1})]
    if answered:events.append(dict(id=3,at=now-50,type='inquiry_responded',patient_id=pid,hospital_id='H02',data={'inquiry_id':1,'action':action}))
    events.append(dict(id=len(events)+1,at=now,type='patient_created',patient_id='P01',hospital_id=None,data={'danger':False}))
    events.append(dict(id=len(events)+1,at=now,type='inquiry_workflow_started',patient_id='P01',hospital_id=None,data={'policy_name':F.NAME,'deadline_seconds':300}))
    prefix=len(events);job=dict(patient_id='P01',version=1,started_at=now,deadline_at=now+300,next_evaluation_at=now,enabled=1,policy_name=F.NAME)
    people=[dict(id='P01',state='WAITING',version=1),dict(id=pid,state='WAITING',version=1)]
    registry=dict(patients=people,jobs=[job],inquiries=[{'patient_id':pid}],decisions=[])
    births=[{k:e[k] for k in ('id','at','type','patient_id')} for e in events if e['type']=='patient_created']
    inquiries=F.public_inquiries(events,now,prefix);inventory=[]
    for patient in people:
        identity=patient['id'];historic=identity==pid;birth=next(e for e in births if e['patient_id']==identity)
        history_rows,pending,own,_=F.horizon._ledger(events,now,identity,prefix)
        qs=[q for q in inquiries if q['patient_id']==identity];contacted={q['hospital_id'] for q in qs}
        inventory.append(dict(patient=dict(patient,reported_priority=None,priority_source='unknown',effective_priority=4),
            patient_created_event=birth,evaluation_end_at=birth['at']+300,job=None if historic else job,
            inquiries=qs,valid_acceptance_ids=[],inputs=dict(now=now,observations=history_rows,pending=pending,
                patient_pending=[dict(q,travel_seconds=current['travel_seconds'][q['hospital']]) for q in own],
                candidates=[dict(hospital_id=h,travel={'duration_seconds':current['travel_seconds'][h]},resources_enough=True) for h in hids if h not in contacted],
                max_new=10-len(contacted),last_contact_at=max((q['last_sent_at'] for q in qs),default=None)),
            apply_snapshot=dict(patient_id=identity,patient_version=1,job_version=None if historic else 1,revision=0,now=now)))
    inventory.sort(key=lambda o:F.owner_key(o['patient']['id']))
    owners=dict(schema_version=1,now=now,event_prefix_max=prefix,decision_prefix_max=0,revision=0,source=F.OWNER_SOURCE,
        first_cohort_patient_ids=['P01'],patient_registry=deepcopy(people),patient_created_events=births,inventory=inventory)
    rows=[]
    for owner in inventory:
        p={k:None for k in F.FULL_PATIENT_KEYS};p.update(id=owner['patient']['id'],state='WAITING',version=1,
            reporter_role='transporter',consent=1,symptoms='가상 실험 환자',required_capabilities=['응급의학'],resource_demands={'ed_bed':1},
            priority=None,priority_source='unknown',lat=current['lat'],lon=current['lon'],requested_at=owner['patient_created_event']['at'],danger_signs=[],extra={})
        j=None if owner['job'] is None else dict({k:None for k in F.FULL_JOB_KEYS},**owner['job'])
        qs=[dict({k:None for k in F.FULL_INQUIRY_KEYS},**q,version=2 if answered else 1) for q in owner['inquiries']]
        rows.append(dict(patient_id=owner['patient']['id'],patient=p,job=j,inquiries=qs))
    source=dict(schema_version=1,now=now,event_prefix_max=prefix,decision_prefix_max=0,revision=0,rows=rows,events=deepcopy(events))
    group=dict(owner_context=owners,owner_source_snapshot=source,registry_snapshot=registry)
    raw=dict(scenario=scenario,branches=[dict(policy=F.NAME,db_events=events,joint_groups=[group])])
    return scenario,events,registry,owners,source,raw


class PublicHistoryProvenanceTests(unittest.TestCase):
    def test_genuine_non_HIST_named_registered_owner_passes_without_mutating_measured_scenario(self):
        scenario,events,registry,owners,source,_=fixture();before=deepcopy((scenario,events,registry,owners,source))
        with self.assertRaisesRegex(ValueError,'owner outside registered scenario'):
            audit._ORIGINAL_LEAF(scenario,events,registry,owners)
        self.assertEqual(audit.corrected_audit_owner_provenance(scenario,events,registry,owners),2)
        stats=audit.audit_history_provenance(scenario,events,registry,owners,source)
        self.assertEqual(stats['historical_owners_verified'],1);self.assertEqual(stats['historical_source_profiles_verified'],1)
        self.assertEqual(stats['profiles'][0]['created_at'],audit.EPOCH-101)
        self.assertEqual(stats['profiles'][0]['travel_source_patient_id'],'P01')
        self.assertEqual((scenario,events,registry,owners,source),before)

    def test_checking_and_unanswered_public_history_use_pending_only_no_automatic_job(self):
        for answered in (True,False):
            scenario,events,registry,owners,source,_=fixture(action='checking',answered=answered)
            stats=audit.audit_history_provenance(scenario,events,registry,owners,source)
            self.assertEqual(audit.corrected_audit_owner_provenance(scenario,events,registry,owners),2)
            item=next(o for o in owners['inventory'] if o['job'] is None)
            self.assertEqual(len(item['inputs']['patient_pending']),1)
            self.assertEqual(item['inquiries'][0]['state'],'CHECKING' if answered else 'PENDING')
            self.assertFalse(stats['profiles'][0]['automatic_job_present'])

    def test_original_current_patient_validation_is_identical(self):
        scenario,events,registry,owners,source,_=fixture()
        owners['inventory']=[o for o in owners['inventory'] if o['patient']['id']=='P01']
        self.assertEqual(audit.corrected_audit_owner_provenance(scenario,events,registry,owners),
            audit._ORIGINAL_LEAF(scenario,events,registry,owners))
        owners['inventory'][0]['inputs']['candidates'].reverse()
        with self.assertRaises(ValueError):audit.corrected_audit_owner_provenance(scenario,events,registry,owners)

    def test_unknown_fake_HIST_id_future_history_or_forged_birth_are_rejected(self):
        scenario,events,registry,owners,_,_=fixture()
        changes=[lambda s,e,r,o:s.__setitem__('history',[]),
            lambda s,e,r,o:s['history'][0].__setitem__('patient_id','HIST_fake'),
            lambda s,e,r,o:s['history'][0].__setitem__('sent_at',0),
            lambda s,e,r,o:s['history'][0].__setitem__('reply_at',1),
            lambda s,e,r,o:o['inventory'][1]['patient_created_event'].__setitem__('at',audit.EPOCH-102),
            lambda s,e,r,o:o['inventory'][1].__setitem__('evaluation_end_at',audit.EPOCH+300),
            lambda s,e,r,o:o['inventory'][1].__setitem__('job',dict(r['jobs'][0],patient_id='old-public-alpha'))]
        for index,change in enumerate(changes):
            s,e,r,o=deepcopy((scenario,events,registry,owners));change(s,e,r,o)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.corrected_audit_owner_provenance(s,e,r,o)

    def test_missing_inquiry_reply_wrong_id_or_new_workflow_cannot_claim_base_history_provenance(self):
        scenario,events,registry,owners,_,_=fixture()
        changes=[lambda e:e[1]['data'].__setitem__('inquiry_id',5),
            lambda e:e[2]['data'].__setitem__('action','accept'),lambda e:e[2].__setitem__('at',audit.EPOCH-40),
            lambda e:e[2].__setitem__('type','inquiry_cancelled'),
            lambda e:e[2].__setitem__('type','inquiry_workflow_started')]
        for index,change in enumerate(changes):
            s,e,r,o=deepcopy((scenario,events,registry,owners));change(e)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.corrected_audit_owner_provenance(s,e,r,o)

    def test_full_guard_clinical_location_demand_and_priority_match_frozen_payload(self):
        scenario,events,registry,owners,source,_=fixture()
        fields={'lat':36.,'required_capabilities':[],'resource_demands':{'ed_bed':2},'symptoms':'changed',
            'priority':2,'reporter_role':'self','requested_at':audit.EPOCH-102}
        for key,value in fields.items():
            forged=deepcopy(source);forged['rows'][1]['patient'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):audit.audit_history_provenance(scenario,events,registry,owners,forged)
        forged=deepcopy(source);forged['events'].pop()
        with self.assertRaises(ValueError):audit.audit_history_provenance(scenario,events,registry,owners,forged)

    def test_history_travel_is_first_public_origin_and_contacted_hospital_is_excluded(self):
        scenario,events,registry,owners,_,_=fixture()
        for mutation in ('travel','contacted','budget','last_contact','own'):
            forged=deepcopy(owners);owner=forged['inventory'][1]
            if mutation=='travel':owner['inputs']['candidates'][0]['travel']['duration_seconds']+=1
            elif mutation=='contacted':owner['inputs']['candidates'].append(dict(hospital_id='H02',travel={'duration_seconds':120},resources_enough=True))
            elif mutation=='budget':owner['inputs']['max_new']=10
            elif mutation=='last_contact':owner['inputs']['last_contact_at']+=1
            else:owner['inquiries'][0]['state']='PENDING'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit.corrected_audit_owner_provenance(scenario,events,registry,forged)

    def test_raw026_double_SHA_verified_public_leaf_and_guard_only(self):
        root=ROOT/'.codex-build/eat-cohort-hedge-20261021';entry=json.loads((root/'repair-raw-index.json').read_text(encoding='utf-8'))['cases'][26]
        self.assertEqual(entry['file'],'026_254238802773411769838001167023330101438.json.gz')
        packed=(root/'repair-cases'/entry['file']).read_bytes()
        self.assertEqual(hashlib.sha256(packed).hexdigest(),entry['compressed_sha256'])
        self.assertEqual(entry['compressed_sha256'],'ae1b8f8f9847505a015368787bb62cc126ef81f14fdbfc5cca65c363dff41541')
        body=gzip.decompress(packed);self.assertEqual(hashlib.sha256(body).hexdigest(),entry['raw_sha256'])
        self.assertEqual(entry['raw_sha256'],'6852c004c8e4567f024b9e97bfd52ac32d74898050e135ce93f1e66cb9b9dab6')
        raw=json.loads(body);branch=raw['branches'][0];g=branch['joint_groups'][0]
        # Only public history/patient profiles, DB events and captured owner
        # sources are accessed. Do not invoke the original replay/trace/math.
        args=(raw['scenario'],branch['db_events'],g['registry_snapshot'],g['owner_context'])
        with self.assertRaisesRegex(ValueError,'owner outside registered scenario'):audit._ORIGINAL_LEAF(*args)
        self.assertEqual(audit.corrected_audit_owner_provenance(*args),len(g['owner_context']['inventory']))
        proof=audit.audit_history_provenance(*args,g['owner_source_snapshot'])
        self.assertEqual(proof['historical_owners_verified'],1)
        self.assertEqual(proof['profiles'][0]['patient_id'],'HIST_H08_3')


class TemporaryReplayHookTests(unittest.TestCase):
    def test_wrapper_calls_captured_original_and_restores_leaf_without_mutating_raw(self):
        _,_,_,_,_,raw=fixture();before=deepcopy(raw);leaf=audit.frozen.audit_owner_provenance
        def original(value):
            b=value['branches'][0];g=b['joint_groups'][0]
            self.assertEqual(F.audit_owner_provenance(value['scenario'],b['db_events'],g['registry_snapshot'],g['owner_context']),2)
            return dict(passed=True,branches=1,new_executions=0)
        with patch.object(audit,'_ORIGINAL_REPLAY',side_effect=original) as called:
            result=audit.audit_replay(raw)
        called.assert_called_once_with(raw);self.assertIs(F.audit_owner_provenance,leaf);self.assertEqual(raw,before)
        self.assertEqual(result['history_provenance_addendum']['historical_owner_occurrences_checked'],1)

    def test_exception_restoration_and_no_hook_installed_during_failed_prepass(self):
        _,_,_,_,_,raw=fixture();leaf=F.audit_owner_provenance
        with patch.object(audit,'_ORIGINAL_REPLAY',side_effect=RuntimeError('synthetic trace error')):
            with self.assertRaisesRegex(RuntimeError,'synthetic trace error'):audit.audit_replay(raw)
        self.assertIs(F.audit_owner_provenance,leaf)
        raw['branches'][0]['joint_groups'][0]['owner_source_snapshot']['rows'][1]['patient']['lat']=36.
        with patch.object(audit,'_ORIGINAL_REPLAY',side_effect=AssertionError('must not run before public guard')) as called:
            with self.assertRaises(ValueError):audit.audit_replay(raw)
        called.assert_not_called();self.assertIs(F.audit_owner_provenance,leaf)

    def test_old_twelve_controls_return_original_proof_without_new_fields(self):
        raw={'branches':[{'policy':'eat_cohort_dynamic_l15_f'}]};proof=dict(passed=True,branches=1,new_executions=0)
        with patch.object(audit,'_ORIGINAL_REPLAY',return_value=proof):result=audit.audit_replay(raw)
        self.assertIs(result,proof);self.assertNotIn('history_provenance_addendum',result)

    def test_clean_import_installs_no_hook_and_loads_no_producer_runner_or_model(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as a;old=a.audit_owner_provenance;from inquiry_lab import cohort_hedge_history_audit as h;assert a.audit_owner_provenance is old;print([n for n in sys.modules if n.startswith('inquiry_lab.') and n.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        r=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(r.stdout.strip(),'[]')


class RecoveryDriverGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This driver has standard-library-only top-level imports. Loading it
        # does not call baseline(), import the experiment or invoke a runner.
        path=ROOT/'.codex-build/eat-cohort-hedge-20261021/recover_audit.py'
        spec=importlib.util.spec_from_file_location('_history_recovery_guard_tests',path)
        cls.driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.driver)

    def check_attestation(self,stage,kind,change=None):
        driver=self.driver;sources={name:'source-'+str(i) for i,name in enumerate(driver.EXTRA)}
        suffixes={'.json','-raw-index.json'}
        if kind=='audit':suffixes.add('-audit.json')
        if stage!='validation':suffixes.add('-lock.json')
        new_runs=int(kind=='execution' and stage!='repair')
        att=dict(passed=True,stage=stage,kind='saved_audit' if kind=='audit' else 'execution_or_saved_recovery',
            audit_addendum_freeze_sha256='sha:audit-addendum-freeze.json',extra_source_sha256=sources,
            baseline_source_count=434,effective_source_count=438,original_source_and_raw_bytes_unchanged=True,
            policy_actions_gates_or_namespace_changed=False,supplementary_dispatch_calls=240,
            stage_evidence_sha256={s:'sha:'+stage+s for s in suffixes},
            new_performance_runs=new_runs,new_simulation_branches=240 if new_runs else 0)
        if change:change(att)
        files={stage+'-supplementary-'+kind+'.json':att,stage+'.json':{'new_branches':240},
            stage+'-audit.json':{'passed':True}}
        def read(path):return files[Path(path).name]
        with patch.object(driver,'verify_addendum',return_value=(object(),{})) as verify, \
                patch.object(driver,'extra_sources',return_value=sources), \
                patch.object(driver,'read',side_effect=read), \
                patch.object(driver,'sha',side_effect=lambda p:'sha:'+Path(p).name):
            result=driver.verify_attestation(stage,kind)
        verify.assert_called_once_with()
        return result

    def test_attestation_requires_exact_stage_suffixes_and_kind_for_both_phases(self):
        for stage in ('repair','development','validation'):
            for kind in ('execution','audit'):
                with self.subTest(stage=stage,kind=kind):self.check_attestation(stage,kind)
                for change in (lambda a:a['stage_evidence_sha256'].pop('-raw-index.json'),
                        lambda a:a['stage_evidence_sha256'].__setitem__('-unexpected.json','forged'),
                        lambda a:a.__setitem__('kind','wrong_phase')):
                    with self.subTest(stage=stage,kind=kind,change=change),self.assertRaises(AssertionError):
                        self.check_attestation(stage,kind,change)
        # A validation lock is deliberately absent; an earlier-stage lock is
        # mandatory. Neither omission nor an extra file can pass the seal.
        with self.assertRaises(AssertionError):self.check_attestation('development','audit',lambda a:a['stage_evidence_sha256'].pop('-lock.json'))
        with self.assertRaises(AssertionError):self.check_attestation('validation','audit',lambda a:a['stage_evidence_sha256'].__setitem__('-lock.json','sha:validation-lock.json'))

    def test_attestation_rejects_missing_source_count_branch_and_dispatch_evidence(self):
        changes=[lambda a:a.__setitem__('baseline_source_count',433),
            lambda a:a.__setitem__('effective_source_count',434),
            lambda a:a.__setitem__('extra_source_sha256',{}),
            lambda a:a.__setitem__('audit_addendum_freeze_sha256','unbound'),
            lambda a:a.__setitem__('supplementary_dispatch_calls',0),
            lambda a:a.__setitem__('supplementary_dispatch_calls',True),
            lambda a:a.__setitem__('new_simulation_branches',239),
            lambda a:a.__setitem__('new_performance_runs',0),
            lambda a:a.__setitem__('original_source_and_raw_bytes_unchanged',False),
            lambda a:a.__setitem__('policy_actions_gates_or_namespace_changed',True)]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(AssertionError):self.check_attestation('development','execution',change)
        with self.assertRaises(KeyError):self.check_attestation('repair','audit',lambda a:a.pop('effective_source_count'))
        with self.assertRaises(AssertionError):self.check_attestation('repair','execution',lambda a:a.__setitem__('new_simulation_branches',240))

    def test_phase_gate_requires_execution_and_audit_attestations_not_audit_alone(self):
        driver=self.driver;proofs={kind:{'verified':kind} for kind in ('execution','audit')}
        with patch.object(driver,'verify_attestation',side_effect=lambda stage,kind:proofs[kind]) as checked:
            self.assertEqual(driver.verify_phase_attestation('repair'),proofs)
        self.assertEqual(checked.call_args_list,[(('repair','execution'),),(('repair','audit'),)])
        for missing in ('execution','audit'):
            def only_one(stage,kind):
                if kind==missing:raise AssertionError('missing '+kind+' attestation')
                return proofs[kind]
            with self.subTest(missing=missing),patch.object(driver,'verify_attestation',side_effect=only_one), \
                    self.assertRaisesRegex(AssertionError,'missing '+missing):driver.verify_phase_attestation('repair')


if __name__=='__main__':unittest.main()
