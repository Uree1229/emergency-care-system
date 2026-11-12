"""Small synthetic equivalence and actual mass-call counts, not a benchmark."""
import ast
from copy import deepcopy
import hashlib
import inspect
import math
from pathlib import Path
import time
import types
import unittest
from inquiry_lab import cohort_recourse_memo_model as memo

original=memo.original
MEASUREMENTS=[]
EXECUTIONS=dict(freeze_owner=0,original_score_owner=0,memo_score_owner=0)


def typed_ordered(value):
    if type(value) is dict:return ('dict',tuple((typed_ordered(k),typed_ordered(v)) for k,v in value.items()))
    if type(value) is list:return ('list',tuple(typed_ordered(v) for v in value))
    if type(value) is tuple:return ('tuple',tuple(typed_ordered(v) for v in value))
    if type(value) is float:return ('float',value.hex())
    return (type(value).__name__,value)


def owner(*,own_count=1,age=30,horizon=65,barrier=15,candidates=2):
    now=1000
    pending=[dict(hospital=f'OLD{i}',patient='OWNER',submitted_at=now-age) for i in range(own_count)]
    own=[dict(q,travel_seconds=30+i) for i,q in enumerate(pending)]
    return dict(patient=dict(id='OWNER',effective_priority=4),evaluation_end_at=now+horizon,
        job=dict(patient_id='OWNER',version=1,started_at=now,deadline_at=now+300,
            next_evaluation_at=now+barrier,enabled=1,policy_name=original.foundation.NAME),
        valid_acceptance_ids=[],inputs=dict(now=now,candidates=[dict(hospital_id=f'H{i}',travel=dict(duration_seconds=i*10)) for i in range(candidates)],
            observations=[],pending=pending,patient_pending=own,max_new=10-own_count,last_contact_at=now-age if own_count else None))


def freeze(o):
    EXECUTIONS['freeze_owner']+=1
    return original.freeze_owner(o)


class CountedH:
    """Private globals for one cloned baseline function; never monkeypatch H."""
    def __init__(self,stats):self.stats=stats
    def __getattr__(self,name):return getattr(original.H,name)
    def masses_at_edges(self,cdf,total,edges):
        self.stats['mass_computations']+=1
        def counted(x):
            self.stats['mass_cdf_evaluations']+=1
            return cdf(x)
        return original.H.masses_at_edges(counted,total,edges)


def baseline(args,stats):
    EXECUTIONS['original_score_owner']+=1
    globals_copy=dict(original.score_owner.__globals__);globals_copy['H']=CountedH(stats)
    f=types.FunctionType(original.score_owner.__code__,globals_copy,'counted_original',original.score_owner.__defaults__,original.score_owner.__closure__)
    return f(*args)


def candidate(args,stats=None,**kw):
    EXECUTIONS['memo_score_owner']+=1
    return memo.score_owner(*args,stats=stats,**kw)


class RecourseMemoTests(unittest.TestCase):
    def compare(self,label,o,*,extras=(),jobs=(),fixed_edit=None):
        fixed=freeze(o)
        if fixed_edit is not None:fixed_edit(fixed)
        row=o['inputs']['pending']+list(extras);args=(o,fixed,row,list(jobs));before=typed_ordered(deepcopy(args))
        old_stats=dict(mass_computations=0,mass_cdf_evaluations=0);stats={}
        started=time.perf_counter();old=baseline(args,old_stats);old_time=time.perf_counter()-started
        started=time.perf_counter();new=candidate(args,stats);new_time=time.perf_counter()-started
        self.assertEqual(typed_ordered(old),typed_ordered(new))
        self.assertEqual(typed_ordered(args),before)
        self.assertEqual(stats['mass_requests'],old_stats['mass_computations'])
        self.assertEqual(stats['mass_computations']+stats['mass_cache_hits'],stats['mass_requests'])
        self.assertLessEqual(stats['mass_computations'],old_stats['mass_computations'])
        MEASUREMENTS.append(dict(fixture=label,candidates=len(o['inputs']['candidates']),horizon=o['evaluation_end_at']-o['inputs']['now'],
            original_mass_calls=old_stats['mass_computations'],memo_mass_calls=stats['mass_computations'],mass_requests=stats['mass_requests'],
            cache_hits=stats['mass_cache_hits'],cache_entries=stats['cache_entries'],uncacheable_requests=stats['uncacheable_requests'],
            original_mass_cdf_calls=old_stats['mass_cdf_evaluations'],memo_mass_cdf_calls=stats['mass_cdf_evaluations'],
            original_instrumented_elapsed_seconds=old_time,memo_instrumented_elapsed_seconds=new_time,
            exact_typed_ordered_floathex_output=True,inputs_unchanged=True))
        return old,new,old_stats,stats

    def test_copied_search_body_only_changes_mass_lookup(self):
        def normalized(function,new=False):
            node=ast.parse(inspect.getsource(function)).body[0]
            if new:
                node.args.kwonlyargs=[];node.args.kw_defaults=[]
                node.body=[n for n in node.body if not (isinstance(n,ast.FunctionDef) and n.name=='mass_item')
                    and not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='mass_memo' for t in n.targets))]
            for n in node.body:
                if isinstance(n,ast.FunctionDef) and n.name=='make_items':n.body=[ast.Pass()]
            return ast.dump(node,include_attributes=False)
        self.assertEqual(normalized(original.score_owner),normalized(memo.score_owner,True))

    def test_foundation_pin_and_module_globals_are_unchanged(self):
        self.assertEqual(hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest(),memo.FOUNDATION_SHA256)
        globals_before={k:id(v) for k,v in original.__dict__.items()};h_before={k:id(v) for k,v in original.H.__dict__.items()}
        self.compare('global_identity',owner())
        self.assertEqual({k:id(v) for k,v in original.__dict__.items()},globals_before)
        self.assertEqual({k:id(v) for k,v in original.H.__dict__.items()},h_before)

    def test_baseline_counter_clone_equals_direct_frozen_function(self):
        o=owner();f=freeze(o);args=(o,f,o['inputs']['pending'],[]);stats=dict(mass_computations=0,mass_cdf_evaluations=0)
        counted=baseline(args,stats);EXECUTIONS['original_score_owner']+=1;direct=original.score_owner(*args)
        self.assertEqual(typed_ordered(counted),typed_ordered(direct))

    def test_normal_two_candidates_has_actual_duplicate_reduction(self):
        _,_,before,after=self.compare('normal_two',owner(horizon=100))
        self.assertGreater(after['mass_cache_hits'],0)
        self.assertLess(after['mass_computations'],before['mass_computations'])
        self.assertLess(after['mass_cdf_evaluations'],before['mass_cdf_evaluations'])

    def test_exact_own_age_zero_180_181(self):
        for age in (0,180,181):
            with self.subTest(age=age):self.compare('own4_age_'+str(age),owner(own_count=4,age=age,horizon=200))

    def test_barrier_partial_final_cell_and_started_clock(self):
        self.compare('barrier7_partial17',owner(age=181,horizon=17,barrier=7))
        o=owner(own_count=0,horizon=17,barrier=90);o['job']['started_at']=1007
        self.compare('no_pending_started7',o)
        o=owner(horizon=45);o['job']['next_evaluation_at']=900
        self.compare('elapsed_barrier',o)

    def test_no_job_accept_and_other_nonplanning_modes(self):
        for label,edit in (
            ('no_job',lambda o:o.update(job=None)),('valid_acceptance',lambda o:o.update(valid_acceptance_ids=[1])),
            ('disabled',lambda o:o['job'].update(enabled=0)),('unsupported',lambda o:o['job'].update(policy_name='fixed_3')),
            ('budget0',lambda o:o['inputs'].update(max_new=0)),('empty_pool',lambda o:o['inputs'].update(candidates=[])),
            ('deadline',lambda o:o['job'].update(deadline_at=1000))):
            o=owner();edit(o)
            with self.subTest(label=label):
                _,_,before,after=self.compare(label,o)
                self.assertEqual(before['mass_computations'],0);self.assertEqual(after['mass_computations'],0)

    def test_virtual_discount_and_unchanged_original_prefix(self):
        for discount in (0.0,5.0,90.0):
            o=owner();inquiry=dict(hospital='H0',patient='FIRST',submitted_at=1000)
            jobs=[dict(hospital='H0',source=dict(inquiry=inquiry),discount_seconds=discount)]
            self.compare('virtual_discount_'+str(discount),o,extras=[inquiry],jobs=jobs)

    def test_six_candidate_bounded_cases(self):
        for label,o in (('six_initial',owner(own_count=0,horizon=65,candidates=6)),
            ('six_existing',owner(horizon=65,candidates=6)),('six_full_occupancy',owner(own_count=4,age=0,horizon=200,candidates=6))):
            with self.subTest(label=label):self.compare(label,o)

    def test_native_ties_and_candidate_input_order(self):
        o=owner(candidates=2,horizon=100)
        for q in o['inputs']['candidates']:q['travel']['duration_seconds']=10
        o['inputs']['candidates'].reverse();self.compare('equal_travel_native_order',o)

    def test_float_barriers_edges_and_input_signed_zero(self):
        o=owner(own_count=0,horizon=17,candidates=2);o['job']['started_at']=1007.0
        _,result,_,_=self.compare('float_offset7',o)
        self.assertIs(type(result['allowed_offsets_seconds'][0]),float)
        o=owner(horizon=17)
        self.compare('float_edges',o,fixed_edit=lambda f:f.update(cell_edges_seconds=[5.0,10.0,15.0,17.0]))
        def signed(f):f['own_items'][0]['masses'][0]=-0.0
        self.compare('signed_zero_input_mass',owner(horizon=17),fixed_edit=signed)

    def test_bool_edges_bypass_cache_without_new_validation(self):
        _,_,before,after=self.compare('bool_edge_bypass',owner(horizon=17),fixed_edit=lambda f:f.update(cell_edges_seconds=[True,5,10,17]))
        self.assertEqual(before['mass_computations'],after['mass_computations']);self.assertEqual(after['mass_cache_hits'],0)

    def test_cache_keys_keep_numeric_types_and_signed_zero(self):
        keys=[memo._mass_key('H',v) for v in (0,0.0,-0.0,1,1.0)]
        self.assertEqual(len(set(keys)),5)
        for v in (True,False,None,float('inf'),float('-inf'),float('nan'),'0'):
            self.assertIsNone(memo._mass_key('H',v))
        self.assertIsNone(memo._mass_key(True,0))

    def test_cached_arrays_are_private_tuples_and_fresh_lists(self):
        stats={};cache=memo._MassMemo(True,True,stats);calls=[]
        def compute():calls.append(1);return [-0.0,0.25,1.0]
        a=cache.get('H',0,compute);a[0]=99
        b=cache.get('H',0,compute);self.assertEqual(b[0].hex(),'-0x0.0p+0');b.reverse()
        c=cache.get('H',0,compute)
        self.assertEqual(typed_ordered(c),typed_ordered([-0.0,.25,1.0]));self.assertEqual(len(calls),1)
        self.assertIs(type(next(iter(cache._values.values()))),tuple);self.assertIsNot(a,b);self.assertIsNot(b,c)

    def test_uncacheable_keys_and_exceptional_computations_follow_original_path(self):
        for offset in (True,float('inf'),float('nan')):
            stats={};cache=memo._MassMemo(True,True,stats);calls=[]
            def compute():calls.append(1);return [0.0]
            cache.get('H',offset,compute);cache.get('H',offset,compute)
            self.assertEqual(len(calls),2);self.assertEqual(stats['mass_cache_hits'],0)
        cache=memo._MassMemo(True,True,{});calls=[]
        def failing():calls.append(1);raise ValueError('same original computation',-0.0)
        for _ in range(2):
            with self.assertRaises(ValueError) as exc:cache.get('H',0,failing)
            self.assertEqual(typed_ordered(exc.exception.args),typed_ordered(('same original computation',-0.0)))
        self.assertEqual(len(calls),2)

    def test_unsupported_context_never_gets_normalized(self):
        row=dict(reply_probability=.8,accept_probability=.5,estimated_wait_seconds=0.0,service_mu=1.0,service_sigma=.7)
        self.assertTrue(memo._cache_safe_context({'H':row},[5,10]))
        for key,value in (('reply_probability',True),('estimated_wait_seconds',float('inf')),('service_mu','1')):
            changed=dict(row);changed[key]=value
            self.assertFalse(memo._cache_safe_context({'H':changed},[5,10]))

    def test_original_error_type_and_arguments_are_preserved(self):
        o=owner();f=freeze(o)
        variants=[(o,dict(f,patient_id='OTHER'),o['inputs']['pending'],[]),
            (o,f,[],[]),(o,f,o['inputs']['pending'],[dict(source=dict(inquiry={}))])]
        bad=deepcopy(o);bad['inputs']['max_new']=True;variants.append((bad,f,bad['inputs']['pending'],[]))
        bad_f=deepcopy(f);bad_f['cell_edges_seconds']=['bad'];variants.append((o,bad_f,o['inputs']['pending'],[]))
        for args in variants:
            found=[]
            for function in (original.score_owner,memo.score_owner):
                EXECUTIONS['original_score_owner' if function is original.score_owner else 'memo_score_owner']+=1
                try:function(*args)
                except Exception as exc:found.append((type(exc).__name__,typed_ordered(exc.args)))
                else:self.fail('invalid synthetic input unexpectedly succeeded')
            self.assertEqual(found[0],found[1])

    def test_explicit_uncached_mode_has_reference_calls_and_output(self):
        o=owner(horizon=100);f=freeze(o);args=(o,f,o['inputs']['pending'],[])
        before=dict(mass_computations=0,mass_cdf_evaluations=0);expected=baseline(args,before);stats={}
        result=candidate(args,stats,memoize=False)
        self.assertEqual(typed_ordered(result),typed_ordered(expected));self.assertEqual(stats['mass_cache_hits'],0)
        self.assertEqual(stats['mass_computations'],before['mass_computations'])
        self.assertEqual(stats['mass_cdf_evaluations'],before['mass_cdf_evaluations'])

    def test_caches_do_not_cross_owner_rows_or_calls(self):
        first=owner();self.compare('independent_call_A',first)
        second=owner();second['patient']['id']='OTHER'
        second['job']['patient_id']='OTHER'
        for q in second['inputs']['pending']+second['inputs']['patient_pending']:q['patient']='OTHER'
        inquiry=dict(hospital='H0',patient='VIRTUAL',submitted_at=1000)
        self.compare('independent_call_B',second,extras=[inquiry],jobs=[dict(hospital='H0',source=dict(inquiry=inquiry),discount_seconds=2.5)])


if __name__=='__main__':unittest.main()
