"""Complete public inventory, including prior no-send and inactive owners."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_hedge_context as context
from test_inquiry_lab_cohort_dynamic_context import fixture as D_fixture


def inventory(joint,members,tails,priority,*,extras=1):
    """Public projections only; no raw, generated scenario or future outcome."""
    joint=deepcopy(joint);members=deepcopy(members);tails=deepcopy(tails);priority=deepcopy(priority)
    first=members+tails;now=joint['now'];old_prefix=joint['event_prefix_max']
    joint['event_prefix_max']+=extras*3;priority['event_prefix_max']=joint['event_prefix_max']
    for m in first:m['inputs']['time_context']['event_prefix_max']=joint['event_prefix_max']
    rows=[];registry=[];events=[]
    priorities={p['patient_id']:p for p in priority['patients']}
    for m,e in zip(first,joint['eligible']):
        pid=m['patient_id'];p=priorities[pid];created=deepcopy(m['inputs']['time_context']['source']['patient_created_event'])
        patient=dict(id=pid,state='WAITING',version=e['patient']['version'],reported_priority=p['reported_priority'],priority_source=p['priority_source'],effective_priority=p['effective_priority'])
        row=dict(patient=patient,patient_created_event=created,evaluation_end_at=created['at']+300,job=deepcopy(e['job']),
            inquiries=[],valid_acceptance_ids=[],inputs={k:deepcopy(v) for k,v in m['inputs'].items() if k!='time_context'},apply_snapshot=deepcopy(m['workflow_snapshot']['apply_snapshot']))
        registry.append(deepcopy(e['patient']));events.append(created);rows.append(row)
    for index in range(extras):
        pid='OWNER'+str(index);created=dict(id=old_prefix+3*index+1,at=now,type='patient_created',patient_id=pid)
        job=dict(patient_id=pid,version=1,started_at=now,deadline_at=now+300,next_evaluation_at=now+15,enabled=1,policy_name=context.POLICY_NAME)
        inp=deepcopy(first[0]['inputs']);inp.pop('time_context');inp.update(patient_pending=[],max_new=10,last_contact_at=None)
        rows.append(dict(patient=dict(id=pid,state='WAITING',version=1,reported_priority=None,priority_source='unknown',effective_priority=4),
            patient_created_event=created,evaluation_end_at=now+300,job=job,inquiries=[],valid_acceptance_ids=[],inputs=inp,
            apply_snapshot=dict(patient_id=pid,patient_version=1,job_version=1,revision=priority['revision'],now=now)))
        registry.append(dict(id=pid,state='WAITING',version=1));events.append(created)
    rows.sort(key=lambda r:context._key(r['patient']['id']));registry.sort(key=lambda r:context._key(r['id']));events.sort(key=lambda r:r['id'])
    value=context.build_owner_context(joint_context=joint,members=members,tail_members=tails,priority_context=priority,
        inventory_rows=rows,registry_patients=registry,events=events)
    return joint,members,tails,priority,value


def fixture(count=4,extras=1):
    registry,_,members,tails,priority=D_fixture(count)
    for j in registry['jobs']:j['policy_name']=context.POLICY_NAME
    for m in members+tails:m['workflow_snapshot']['job']['policy_name']=context.POLICY_NAME
    joint=context.build_context(**registry)
    return inventory(joint,members,tails,priority,extras=extras)


def checked(joint,members,tails,priority,value):
    return context.validate_owner_context(value,joint_context=joint,members=members,tail_members=tails,priority_context=priority)


class CohortHedgeContextTests(unittest.TestCase):
    def test_complete_partition_includes_prior_no_send_zero_query_owner(self):
        args=fixture();joint,members,tails,priority,value=args
        self.assertEqual(len(value['inventory']),5)
        self.assertEqual(value['first_cohort_patient_ids'],[m['patient_id'] for m in members+tails])
        owner=next(r for r in value['inventory'] if r['patient']['id']=='OWNER0')
        self.assertEqual(owner['inquiries'],[])
        self.assertEqual(checked(*args),value)

    def test_omitted_owner_birth_duplicate_and_order_fail_closed(self):
        joint,members,tails,priority,value=fixture()
        variants=[]
        for mode in ('omit','duplicate','reverse','birth','prefix'):
            bad=deepcopy(value)
            if mode=='omit':bad['inventory']=bad['inventory'][1:]
            elif mode=='duplicate':bad['inventory'].append(deepcopy(bad['inventory'][0]))
            elif mode=='reverse':bad['inventory'].reverse()
            elif mode=='birth':bad['patient_created_events'].pop()
            else:bad['event_prefix_max']+=1
            variants.append(bad)
        for bad in variants:
            with self.assertRaises(ValueError):checked(joint,members,tails,priority,bad)

    def test_all_public_owner_priority_versions_and_barriers_checked(self):
        j,ms,ts,p,v=fixture()
        for path,new in [(('patient','effective_priority'),2),(('patient','version'),2),(('apply_snapshot','revision'),8),
            (('apply_snapshot','job_version'),2),(('inputs','now'),1001),(('inputs','max_new'),9)]:
            bad=deepcopy(v);bad['inventory'][0][path[0]][path[1]]=new
            with self.assertRaises(ValueError):checked(j,ms,ts,p,bad)
        bad=deepcopy(v);first=next(r for r in bad['inventory'] if r['patient']['id']==ms[0]['patient_id']);first['job']['next_evaluation_at']+=1
        with self.assertRaises(ValueError):checked(j,ms,ts,p,bad)

    def test_nullable_disabled_unsupported_owner_remains_in_inventory(self):
        j,ms,ts,p,v=fixture()
        for job in (None,dict(v['inventory'][0]['job'],enabled=0),dict(v['inventory'][0]['job'],policy_name='fixed_3')):
            bad=deepcopy(v);bad['inventory'][0]['job']=job
            if job is None:bad['inventory'][0]['apply_snapshot']['job_version']=None
            self.assertEqual(len(checked(j,ms,ts,p,bad)['inventory']),5)

    def test_beyond_H_nonwaiting_and_future_birth_inventory_rules(self):
        j,ms,ts,p,v=fixture()
        bad=deepcopy(v);owner=bad['inventory'][0];pid=owner['patient']['id']
        next(r for r in bad['patient_registry'] if r['id']==pid)['state']='SELECTED'
        bad['inventory'].pop(0);self.assertEqual(len(checked(j,ms,ts,p,bad)['inventory']),4)
        bad=deepcopy(v);bad['patient_created_events'][-1]['at']=j['now']+1
        with self.assertRaises(ValueError):checked(j,ms,ts,p,bad)
        bad=deepcopy(v);bad['patient_created_events'][-1]['at']=j['now']-300;bad['inventory'].pop(0)
        self.assertEqual(len(checked(j,ms,ts,p,bad)['inventory']),4)

    def test_input_mutation_and_native_D_alias_do_not_escape(self):
        args=fixture();before=deepcopy(args);checked(*args)
        j,ms,ts,p,v=args
        self.assertEqual(context.base_context_projection(j)['eligible'][0]['job']['policy_name'],context.BASE_POLICY_NAME)
        self.assertEqual(context.base_members_projection(ms)[0]['workflow_snapshot']['job']['policy_name'],context.BASE_POLICY_NAME)
        self.assertEqual(args,before)

    def test_expert_tiers_unknown_and_new_fields(self):
        for t in (4,2,1):
            self.assertEqual(context.effective_priority(t,'expert'),t)
            self.assertEqual(context.effective_priority(t,'self'),4)
        j,ms,ts,p,v=fixture();bad=deepcopy(v);bad['inventory'][0]['hidden_service']=1
        with self.assertRaises(ValueError):checked(j,ms,ts,p,bad)


if __name__=='__main__':unittest.main()
