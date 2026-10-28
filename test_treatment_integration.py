"""공개 치료조건의 화면·환자 저장·배정 연결. 실 API/인증키/실 DB를 사용하지 않는다."""
import json

from server import db, hira_data, planning, public_data, service, solver_adapter, treatment_binding, worker
from server.treatment_data import evaluate_requirements
from solver_improved import solve
from test_server import Base, EST, T0, base_patient


class TreatmentIntegration(Base):
    def public_hospital(self, hid='H1'):
        h = service.hospital_view(self.conn, hid, T0)
        service.upsert_hospital(self.conn, {**h, 'source':'public', 'capabilities':[],
                                           'capabilities_status':'unknown'})

    def observe(self, endpoint, normalized, *, hid='H1', observed_at=None, now=T0):
        public_data._save_snapshot(self.conn, hid, endpoint, now, observed_at, normalized, {'fixture':True})

    def plan(self, now=T0 + 20, runner=None):
        return worker.run_once(self.path, EST, clock=lambda:now, seconds=0,
                               runner=runner or (lambda model,seconds:solve(model,seconds=seconds)))

    def test_profile_id_requires_explicit_valid_catalog_selection(self):
        for profile_id in ('UNKNOWN', '', 12, None, ['TR_GENERAL_ED']):
            with self.subTest(profile_id=profile_id):
                self.assertEqual(self.code(service.create_patient, self.conn, T0, 'BAD',
                    base_patient(extra={'treatment_profile_id':profile_id})), 'INVALID')
        patient = self.patient(symptoms='고위험 산모 신생아 수술', extra={})
        self.assertEqual(patient['resource_demands'], {'ed_bed':1})
        self.assertEqual(patient['required_capabilities'], [])
        self.assertIsNone(treatment_binding.requirements_for(patient)['profile_id'])

    def test_profile_extended_and_explicit_requirements_are_persisted_as_max_union(self):
        patient = self.patient(extra={'treatment_profile_id':'TR_NEUROSURGICAL'},
            required_capabilities=['응급의학'],
            resource_demands={'ed_bed':2, 'operating_room':3, 'custom_resource':1})
        self.assertEqual(patient['required_capabilities'], ['신경외과','응급의학'])
        expected = {'ed_bed':2,'special_team':1,'operating_room':3,'icu_bed':1,'custom_resource':1}
        self.assertEqual(patient['resource_demands'], expected)
        stored = db.row(self.conn.execute("SELECT * FROM patients WHERE id='A'").fetchone())
        self.assertEqual(stored['resource_demands'], expected)
        # 명시 값이 프로필보다 작아져도 필수 자원이 사라지지 않는다.
        updated = service.update_patient(self.conn, T0 + 1, 'A', {'resource_demands':{'ed_bed':1}})
        self.assertEqual(updated['resource_demands'],
                         {'ed_bed':1,'special_team':1,'operating_room':1,'icu_bed':1})

    def test_profile_added_during_update_keeps_existing_explicit_demands(self):
        self.patient(required_capabilities=['응급의학'], resource_demands={'ed_bed':2,'operating_room':2})
        updated = service.update_patient(self.conn, T0 + 1, 'A',
            {'extra':{'treatment_profile_id':'TR_HIGH_RISK_BIRTH_NEONATAL'}})
        self.assertEqual(updated['required_capabilities'], ['산부인과','소아청소년과','응급의학'])
        self.assertEqual(updated['resource_demands'],
                         {'ed_bed':2,'special_team':1,'delivery_room':1,'nicu_bed':1,'operating_room':2})
        sent = service.send_inquiries(self.conn, T0 + 2, 'A', ['H1'])
        hospital = service.hospital_inquiries(self.conn, T0 + 3, 'H1')
        self.assertEqual(hospital['open'][0]['patient']['resource_demands'], sent['resource_demands'])

    def test_profile_change_and_removal_replace_derived_conditions_but_keep_explicit_conditions(self):
        self.patient(extra={'treatment_profile_id':'TR_HIGH_RISK_BIRTH_NEONATAL'},
                     required_capabilities=['정형외과'],resource_demands={'ed_bed':2,'operating_room':2})
        changed = service.update_patient(self.conn,T0+1,'A',{'extra':{'treatment_profile_id':'TR_GENERAL_ED'}})
        self.assertEqual(changed['required_capabilities'],['응급의학','정형외과'])
        self.assertEqual(changed['resource_demands'],{'ed_bed':2,'operating_room':2})
        self.assertNotIn('nicu_bed',changed['resource_demands'])
        self.assertNotIn('delivery_room',changed['resource_demands'])
        removed = service.update_patient(self.conn,T0+2,'A',{'extra':{}})
        self.assertEqual(removed['required_capabilities'],['정형외과'])
        self.assertEqual(removed['resource_demands'],{'ed_bed':2,'operating_room':2})
        self.assertEqual(removed['explicit_required_capabilities'],['정형외과'])
        self.assertEqual(removed['explicit_resource_demands'],{'ed_bed':2,'operating_room':2})

    def test_explicit_capability_uses_only_catalog_verified_exact_registered_names(self):
        self.public_hospital()
        self.observe('hira_departments',hira_data.normalize_records('hira_departments',
                     [{'dgsbjtCdNm':'응급의학과','dgsbjtPrSdrCnt':'4'}]))
        self.patient(required_capabilities=['응급의학'])
        candidate = next(c for c in service.candidates(self.conn,T0,'A',EST)['candidates'] if c['hospital_id']=='H1')
        evidence = next(e for e in candidate['treatment_evaluation']['evidence'] if e['kind']=='department')
        self.assertEqual(evidence['status'],'MATCH')
        self.assertEqual(evidence['accepted_exact_names'],['응급의학과'])

    def test_hospital_and_candidates_expose_hira_evidence_without_projecting_capacity(self):
        self.public_hospital()
        before = [dict(r) for r in self.conn.execute('SELECT * FROM resources')]
        self.observe('hira_departments', hira_data.normalize_records('hira_departments',
            [{'dgsbjtCd':'23','dgsbjtCdNm':'응급의학과','dgsbjtPrSdrCnt':'4'}]))
        self.observe('hira_facilities', hira_data.normalize_records('hira_facilities', [{'emymCnt':'999'}]))
        self.patient(extra={'treatment_profile_id':'TR_GENERAL_ED'})
        hospital = service.hospital_view(self.conn, 'H1', T0)
        self.assertEqual(len(hospital['treatment_evaluations']), 6)
        candidate = next(c for c in service.candidates(self.conn, T0, 'A', EST)['candidates']
                         if c['hospital_id']=='H1')
        evaluation = candidate['treatment_evaluation']
        self.assertEqual(evaluation['profile_id'], 'TR_GENERAL_ED')
        department = next(e for e in evaluation['evidence'] if e['kind']=='department')
        self.assertEqual((department['status'],department['endpoint']), ('MATCH','hira_departments'))
        resource = next(e for e in evaluation['evidence'] if e['kind']=='resource')
        self.assertIsNone(resource['reported_available'])
        self.assertEqual(resource['registered_evidence'][0]['reported_count'], 999)
        self.assertTrue(evaluation['patient_acceptance_required'])
        self.assertTrue(any('ed_bed' in c for c in candidate['checks']))
        self.assertEqual([dict(r) for r in self.conn.execute('SELECT * FROM resources')], before)

    def test_api_evidence_contains_relevant_conditions_and_keeps_full_inventory_in_source_and_research(self):
        self.public_hospital()
        self.observe('hira_departments',hira_data.normalize_records('hira_departments',[
            {'dgsbjtCdNm':'응급의학과','dgsbjtPrSdrCnt':'4'},
            {'dgsbjtCdNm':'정형외과','dgsbjtPrSdrCnt':'3'}]))
        self.observe('hira_facilities',hira_data.normalize_records('hira_facilities',[{'emymCnt':'10','nbySprmCnt':'2'}]))
        equipment = [{'oftCd':f'UNRELATED-{i}','oftCdNm':f'관련 없는 등록장비 {i}','oftCnt':'1'} for i in range(100)]
        self.observe('hira_equipment',hira_data.normalize_records('hira_equipment',equipment))
        patient = self.patient(extra={'treatment_profile_id':'TR_GENERAL_ED'})
        observations = treatment_binding.observation_map(public_data.snapshot_view(self.conn,'H1',T0),T0)
        requirements = treatment_binding.requirements_for(patient)
        full = evaluate_requirements(requirements,observations=observations,now=T0)
        compact = treatment_binding.evaluate_patient(patient,observations,T0)
        self.assertEqual(compact['registered_evidence_scope'],'requested_conditions')
        self.assertEqual(compact['available_registered_evidence_count'],len(full['registered_evidence']))
        self.assertLess(len(compact['registered_evidence']),len(full['registered_evidence']))
        self.assertFalse(any(r['kind']=='registered_equipment' for r in compact['registered_evidence']))
        self.assertTrue(any(r.get('name')=='응급의학과' for r in compact['registered_evidence']))
        self.assertEqual((compact['status'],compact['evidence'],compact['unknown'],compact['excluded']),
                         (full['status'],full['evidence'],full['unknown'],full['excluded']))
        identities = [json.dumps(r,sort_keys=True,ensure_ascii=False) for r in compact['registered_evidence']]
        self.assertEqual(len(identities),len(set(identities)))
        self.assertLess(len(json.dumps(compact)),len(json.dumps(full)))
        hospital = service.hospital_view(self.conn,'H1',T0)
        self.assertTrue(all(e['registered_evidence_scope']=='requested_conditions'
                            for e in hospital['treatment_evaluations'].values()))
        source = next(s for s in hospital['public_data'] if s['endpoint']=='hira_equipment')
        self.assertEqual(len(source['data']['registered_equipment']),100)
        # API 정리 이후에도 순수 evaluator의 연구용 전체 재고는 줄어들지 않는다.
        self.assertEqual(len(evaluate_requirements(requirements,observations=observations,now=T0)
                             ['registered_evidence']),len(full['registered_evidence']))

    def test_public_zero_and_stale_observation_keep_candidate_for_confirmation(self):
        self.public_hospital()
        self.patient()
        for age in (0, 700):
            with self.subTest(age=age):
                self.observe('nmc_rltm_beds', {'resources':[{'resource_type':'ed_bed',
                    'reported_available':0,'status':'reported','source_field':'hvec'}]}, observed_at=T0-age)
                candidates = service.candidates(self.conn, T0, 'A', EST)
                candidate = next(c for c in candidates['candidates'] if c['hospital_id']=='H1')
                self.assertEqual(candidate['treatment_evaluation']['status'], 'NEEDS_CONFIRMATION')
                self.assertEqual(candidate['treatment_evaluation']['excluded'], [])
                self.assertEqual(candidate['treatment_evaluation']['stale'], age>600)
        service.add_restriction(self.conn, T0, 'H1', 'all', '담당자 현재 불가 확인')
        candidates = service.candidates(self.conn, T0, 'A', EST)
        self.assertEqual(next(e for e in candidates['excluded'] if e['hospital_id']=='H1')['reason'], 'RESTRICTED')

    def test_public_unknown_does_not_override_valid_acceptance_with_operational_capacity(self):
        self.public_hospital()
        self.patient(extra={'treatment_profile_id':'TR_GENERAL_ED'})
        self.plan()
        self.assertEqual(planning.patient_result(self.conn,T0+20,'A')['result']['status'], 'UNASSIGNED')
        iid = self.accepted('A','H1')
        self.plan()
        result = planning.patient_result(self.conn,T0+20,'A')['result']
        self.assertEqual((result['status'],result['hospital_id'],result['inquiry_id']), ('RECOMMENDED','H1',iid))
        self.assertTrue(result['confirmable_now'])
        self.assertEqual(result['treatment_evaluation']['status'], 'NEEDS_CONFIRMATION')
        self.assertIn('TREATMENT_CONFIRMATION_REQUIRED:H1', result['warnings'])
        self.assertEqual(result['treatment_requirements']['profile_id'], 'TR_GENERAL_ED')

    def test_registered_capacity_does_not_supply_missing_operational_capacity(self):
        self.public_hospital()
        self.conn.execute("DELETE FROM resources WHERE hospital_id='H1'")
        self.observe('hira_facilities', hira_data.normalize_records('hira_facilities', [{'emymCnt':'999'}]))
        self.patient()
        self.accepted('A','H1')
        with db.transaction(self.conn):
            snapshot = planning.capture(self.conn,T0+20)
        model, context = solver_adapter.build(snapshot,EST)
        self.assertEqual(model['capacities'][0], [0,0])
        self.assertEqual(context['A']['eligible'], [])
        self.plan()
        result = planning.patient_result(self.conn,T0+20,'A')['result']
        self.assertFalse(result['confirmable_now'])
        self.assertIsNone(result['hospital_id'])

    def test_extended_profile_is_reviewed_instead_of_omitting_or_and_icu(self):
        self.public_hospital()
        self.patient(extra={'treatment_profile_id':'TR_NEUROSURGICAL'})
        self.accepted('A','H1')
        self.plan()
        result = planning.patient_result(self.conn,T0+20,'A')['result']
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        self.assertIn('UNSUPPORTED_RESOURCE', result['warnings'])
        self.assertEqual(result['treatment_requirements']['resource_demands']['operating_room'],1)
        self.assertEqual(result['treatment_requirements']['resource_demands']['icu_bed'],1)
        self.assertFalse(result['confirmable_now'])

    def test_public_change_while_solver_runs_invalidates_result(self):
        self.patient()
        self.accepted('A','H1')
        def updating_solver(model, seconds):
            other = db.connect(self.path)
            try:
                public_data._save_snapshot(other,'H1','hira_departments',T0+20,None,
                    hira_data.normalize_records('hira_departments',[]), {'fixture':True})
            finally:
                other.close()
            return solve(model,seconds=0)
        self.assertFalse(self.plan(runner=updating_solver))
        self.assertIsNone(planning.patient_result(self.conn,T0+20,'A')['result'])

    def test_public_snapshot_capture_and_revision_are_transactional_and_delete_invalidates(self):
        self.observe('hira_departments', hira_data.normalize_records('hira_departments',[]))
        with db.transaction(self.conn):
            captured = planning.capture(self.conn,T0)
        self.assertEqual(len(captured['public_snapshots']),1)
        revision = captured['revision']
        try:
            with db.transaction(self.conn):
                self.observe('hira_departments', hira_data.normalize_records('hira_departments',
                    [{'dgsbjtCdNm':'응급의학과'}]))
                raise RuntimeError('rollback')
        except RuntimeError:
            pass
        self.assertEqual(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)
        self.conn.execute('DELETE FROM public_api_snapshots')
        self.assertGreater(self.conn.execute('SELECT revision FROM planning_state').fetchone()[0], revision)
        self.assertEqual(json.loads(captured['public_snapshots'][0]['normalized'])['record_count'],0)


if __name__=='__main__':
    import unittest
    unittest.main()
