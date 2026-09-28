"""Search budget sensitivity on six selected synthetic instances."""
from solver_experiment import *

records = []
for scenario in ['normal', 'surge']:
    for seed in range(3):
        observed, actual = generate(seed, scenario)
        hint = greedy(observed)
        for budget in [.05, .5, 2]:
            plan, meta = solve(observed, hint, budget)
            validate(observed, plan)
            records.append(dict(scenario=scenario, seed=seed, deterministic_budget=budget,
                                **meta, **{k:v for k,v in replay(actual,plan).items() if k!='execution'}))
        print('done',scenario,seed,flush=True)
(OUT/'sensitivity.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(records,ensure_ascii=False,indent=2))
