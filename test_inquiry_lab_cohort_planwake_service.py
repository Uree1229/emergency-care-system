"""Identical SQL service bodies with local workflow lookup and no registry patch."""
from copy import deepcopy
import ast
import inspect
import unittest
from server import inquiry_policies,service as old
from inquiry_lab import cohort_planwake_service as s,cohort_planwake_policy as p,cohort_planwake_workflow as w
from test_inquiry_lab_cohort_planwake_workflow import DBFixture


class PlanwakeServiceTests(DBFixture):
    def test_four_entry_point_bodies_preserve_all_nonimport_AST_nodes(self):
        def normalized(function):
            tree=ast.parse(inspect.getsource(function))
            class Imports(ast.NodeTransformer):
                def visit_Import(self,node):return None
                def visit_ImportFrom(self,node):return None
            return ast.dump(Imports().visit(tree),include_attributes=False)
        for name in ('create_patient','patient_view','confirm_destination','cancel_patient'):
            self.assertEqual(normalized(getattr(s,name)),normalized(getattr(old,name)))

    def test_creation_and_view_work_without_installing_candidate_in_original_registry(self):
        self.assertNotIn(p.NAME,inquiry_policies._POLICIES)
        registry=deepcopy(inquiry_policies._POLICIES);value=s.patient_view(self.conn,'P',1000)
        self.assertEqual(value['inquiry_workflow']['policy']['name'],p.NAME)
        self.assertEqual(inquiry_policies._POLICIES,registry)

    def test_duplicate_creation_rolls_back_without_partial_events_or_workflow(self):
        before=self.state()
        with self.assertRaises(old.ServiceError):
            s.create_patient(self.conn,1001,'P',dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],resource_demands={'ed_bed':1},
                lat=35.,lon=127.,auto_inquiry=True,inquiry_policy=p.NAME))
        self.assertEqual(self.state(),before)

    def test_confirm_and_cancel_keep_the_local_workflow_view_callable(self):
        self.seed();iid=self.conn.execute("SELECT id FROM inquiries WHERE hospital_id='H1'").fetchone()[0]
        old.respond(self.conn,1005,iid,'accept','unit')
        value=s.confirm_destination(self.conn,1005,'P',iid)
        self.assertEqual(value['state'],'CONFIRMED');self.assertFalse(value['inquiry_workflow']['enabled'])
        value=s.cancel_patient(self.conn,1006,'P','unit')
        self.assertEqual(value['state'],'CANCELLED');self.assertFalse(w._job(self.conn,'P')['enabled'])


if __name__=='__main__':unittest.main()
