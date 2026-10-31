"""Small controlled sensitivity run. No empirical population calibration."""
import json
from pathlib import Path
from collections import Counter
from solver_improved import solve, greedy, score, validate, metrics, capacity_ok
from solver_experiment import generate, CAP

rows=[]
for scenario in ('normal', 'surge', 'stale_capacity', 'unobserved_inflow', 'time_error'):
    for seed in range(4):
        observed, actual=generate(seed,scenario)
        def adapt(raw):
            return dict(version=1,now=0,horizon=24,capacities=CAP,blocks=raw['blocks'],fixed={},
                        patients=[dict(id=str(p['id']),priority=p['weight'],special=p['special'],release=0,
                                       deadline=12 if p['weight']==4 else 24,duration=p['duration'],
                                       travel=p['travel'],eligible=list(range(2 if p['special'] else 3)))
                                  for p in raw['patients']])
        data=adapt(observed); changed=adapt(actual)
        baseline=greedy(data)
        for policy in ('total_first','severity_first'):
            plan,meta=solve(data,policy,seconds=0.25,hint=baseline)
            assert validate(data,plan)
            assert score(data,plan,policy)<=score(data,baseline,policy)
            rows.append(dict(scenario=scenario,seed=seed,policy=policy,metrics=metrics(data,plan),meta=meta,
                             still_valid_under_hidden_state=validate(changed,plan),
                             baseline_objective=list(score(data,baseline,policy))))
    print('completed',scenario,flush=True)
summary=dict(runs=len(rows),all_observed_valid=True,all_not_worse_than_baseline=True,
             statuses=dict(Counter(r['meta']['status'] for r in rows)),
             lex_optimal=sum(r['meta']['lex_optimal'] for r in rows),
             fallbacks=sum(r['meta']['fallback'] for r in rows),
             max_elapsed_seconds=max(r['meta']['elapsed_seconds'] for r in rows),
             hidden_state_invalid_by_scenario={s:sum(not r['still_valid_under_hidden_state'] for r in rows if r['scenario']==s)
                                              for s in ('normal','surge','stale_capacity','unobserved_inflow','time_error')})
out=Path('optimization_results_v2');out.mkdir(exist_ok=True)
(out/'sensitivity.json').write_text(json.dumps(dict(summary=summary,runs=rows),ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
