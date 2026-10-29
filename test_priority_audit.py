"""Synthetic fixtures test the independent audit before a new holdout exists."""
from copy import deepcopy
from pathlib import Path
import statistics
import unittest

from simulation.priority_audit import (CONSUMED_VALIDATION_SEEDS, guard_output_path,
    audit_development_payload, audit_results, paired_priority, priority_totals,
    tradeoffs, validate_fresh_seeds, validate_map_replays, validate_priority_records)


def row(policy, seed, starts, asks=(1, 1, 1)):
    outcomes = [dict(id=pid, priority=priority, search_start_t=10,
        first_commit_t=11 if start is not None else None,
        final_arrive_t=12 if start is not None else None, treatment_start_t=start,
        inquiries=ask) for pid, priority, start, ask in zip(('A', 'B', 'C'), (4, 2, 1), starts, asks)]
    metrics = dict(patients=len(outcomes), inquiries=sum(asks), inquiries_per_patient=sum(asks) / len(outcomes))
    for field, count, mean in (('first_commit_t', 'commit_observed', 'commit_mean_minutes'),
                               ('final_arrive_t', 'arrival_observed', 'arrival_mean_minutes'),
                               ('treatment_start_t', 'start_observed', 'start_mean_minutes')):
        times = [o[field] - o['search_start_t'] for o in outcomes if o[field] is not None]
        metrics[count], metrics[mean] = len(times), statistics.mean(times) if times else None
    metrics.update(not_treated=len(outcomes) - metrics['start_observed'],
                   not_arrived=len(outcomes) - metrics['arrival_observed'])
    priorities = priority_totals([dict(outcomes=outcomes)])
    return dict(policy_id=policy, scenario_id='S', seed=seed, outcomes=outcomes, metrics=metrics,
        priority={tier: dict(total=data['patients'], start_observed=data['treatment_start_observed'],
            unassigned=data['unassigned'], start_max_minutes=data['start_max_minutes'])
            for tier, data in priorities.items()})


def records():
    return [row('BASE', 5001, (None, 30, 40)), row('P', 5001, (20, None, 30)),
            row('BASE', 5002, (20, 30, 40)), row('P', 5002, (22, 29, 38))]


def map_fixture():
    conditions = (('mixed_priority_scarcity', 0), ('staggered_priority', .2), ('resource_change', 0))
    policies = {'BASE', 'P', 'P2', 'P3', 'P4'}
    payload = dict(validation_seeds=[5001, 5002], replay_cases=[])
    export, viewer, cells = dict(scenarios=[]), dict(scenarios=[]), {}
    for family, nonresponse in conditions:
        sid = 'priority_' + family
        selection = dict(scenario_id=sid, seed=5001, baseline_id='BASE', policy_id='P',
                         difference=-2, common_patients=3, outcome_independent_selection=True)
        payload['replay_cases'].append(selection)
        scenario = dict(id=sid + '_seed5001', seed=5001, case_kind=family, experiment_nonresponse=nonresponse,
            experiment_selection=selection, experiment_environment={'kind': 'priority_protection_validation'},
            patients=[{'id': 'A'}, {'id': 'B'}, {'id': 'C'}], hospitals=[{'id': 'H'}],
            response_environment={}, exogenous_events=[], inquiry_queue={})
        export['scenarios'].append(dict(scenario=scenario, **selection))
        branches, cell = [], {}
        for pid in sorted(policies):
            record = row(pid, 5001, (20, 30, 40) if pid == 'BASE' else (18, 28, 38))
            record.update(scenario_id=sid, policy={'name': pid})
            cell[pid] = record
            branches.append(dict(id=pid, policy=record['policy'],
                                 patient_outcomes=deepcopy(record['outcomes'])))
        cells[(sid, 5001)] = cell
        viewer['scenarios'].append(dict(scenario=deepcopy(scenario), branches=branches))
    return payload, export, viewer, cells, policies, conditions


def experiment_fixture():
    """Use the real runner's serialization with synthetic outcome injection."""
    from simulation.priority_experiment import REPLAY_CONDITIONS, export_replay_cases, resume_validation, run_experiment
    from test_priority_experiment import generator
    from test_workload_experiment import scenario
    development, validation = [], []
    for family, nonresponse in REPLAY_CONDITIONS:
        for split, target in (('development', development), ('validation', validation)):
            value = scenario(f'{family}_{split}')
            value.update(case_kind=family, experiment_nonresponse=nonresponse, split=split)
            target.append(value)
    saved = run_experiment(development, validation, [301, 302], [5001, 5002],
                          generator=generator, bootstrap_samples=100, development_only=True)
    complete = resume_validation(saved, development, validation, [301, 302], [5001, 5002],
                                 generator=generator, bootstrap_samples=100)
    exported = export_replay_cases(complete, validation)
    viewer = dict(scenarios=[])
    for item in exported['scenarios']:
        selection = item['scenario']['experiment_selection']
        rows = [r for r in complete['suites']['validation']['records']
                if r['scenario_id'] == selection['scenario_id'] and r['seed'] == selection['seed']]
        viewer['scenarios'].append(dict(scenario=deepcopy(item['scenario']), branches=[
            dict(id=r['policy_id'], policy=deepcopy(r['policy']), patient_outcomes=deepcopy(r['outcomes']))
            for r in rows]))
    return complete, saved, exported, viewer


class PriorityAuditTests(unittest.TestCase):
    def cells(self, values=None):
        return validate_priority_records(values or records(), {'BASE', 'P'}, {('S', 5001), ('S', 5002)})

    def test_priority_denominators_preserve_censored_patients(self):
        totals = priority_totals([records()[0]])
        self.assertEqual(totals['4']['patients'], 1)
        self.assertEqual(totals['4']['untreated'], 1)
        self.assertEqual(totals['4']['not_arrived'], 1)
        self.assertIsNone(totals['4']['conditional_start_mean_minutes'])

    def test_generic_and_priority_counts_are_independently_checked(self):
        values = records()
        before = deepcopy(values)
        self.cells(values)
        self.assertEqual(values, before)
        for target, key, value in (('metrics', 'not_treated', 0), ('metrics', 'inquiries_per_patient', 999)):
            changed = deepcopy(values)
            changed[0][target][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.cells(changed)
        changed = deepcopy(values)
        changed[0]['priority']['4']['start_observed'] = 1
        with self.assertRaises(ValueError):
            self.cells(changed)

    def test_missing_and_duplicate_comparisons_fail(self):
        for changed in (records()[:-1], records() + [deepcopy(records()[0])]):
            with self.subTest(count=len(changed)), self.assertRaises(ValueError):
                self.cells(changed)

    def test_high_tier_time_pairs_only_same_observed_patient(self):
        result = paired_priority(self.cells(), 'BASE', 'P', tier=4, samples=100)
        self.assertEqual(result['common_patients'], 1)
        self.assertEqual(result['seeds_with_common_times'], 1)
        self.assertEqual(result['mean_difference_minutes'], 2)
        self.assertEqual(result['interval_95_minutes'], [2, 2])
        self.assertEqual(result['observed_identity_switches'],
            [dict(scenario_id='S', seed=5001, baseline_only=[], selected_only=['A'])])

    def test_all_censored_group_reports_missing_interval_not_fake_zero(self):
        values = [row('BASE', 5001, (None, 20, 30)), row('P', 5001, (None, 22, 28))]
        cells = validate_priority_records(values, {'BASE', 'P'}, {('S', 5001)})
        result = paired_priority(cells, 'BASE', 'P', tier=4, samples=100)
        self.assertEqual(result['common_patients'], 0)
        self.assertIsNone(result['mean_difference_minutes'])
        self.assertIsNone(result['interval_95_minutes'])

    def test_high_tier_gain_and_other_tier_loss_are_not_hidden_by_equal_total(self):
        result = tradeoffs(self.cells(), 'BASE', 'P')
        self.assertEqual(result['total_untreated_difference'], 0)
        self.assertEqual(result['priority_untreated_differences'], {'4': -1, '2': 1, '1': 0})
        self.assertEqual(result['high_priority_gain_with_other_tier_loss_cells'], 1)
        self.assertEqual(result['high_priority_untreated_regression_cells'], 0)
        reverse = tradeoffs(self.cells(), 'P', 'BASE')
        self.assertEqual(reverse['high_priority_untreated_regression_cells'], 1)

    def test_high_tier_improvement_does_not_hide_increased_total_untreated(self):
        values = [row('BASE', 5001, (None, 20, 30)), row('P', 5001, (20, None, None))]
        cells = validate_priority_records(values, {'BASE', 'P'}, {('S', 5001)})
        result = tradeoffs(cells, 'BASE', 'P')
        self.assertEqual(result['priority_untreated_differences'], {'4': -1, '2': 1, '1': 1})
        self.assertEqual(result['total_untreated_difference'], 1)
        self.assertEqual(result['total_untreated_regression_cells'], 1)
        self.assertEqual(result['high_priority_gain_with_other_tier_loss_cells'], 1)

    def test_seed_plan_cannot_reuse_consumed_validation_or_mutate_after_lock(self):
        plan = dict(development_seeds=[301, 302], validation_seeds=[5001, 5002],
                    previous_validation_seeds_excluded=sorted(CONSUMED_VALIDATION_SEEDS))
        validate_fresh_seeds(plan, deepcopy(plan))
        for key, values in (('validation_seeds', [3001, 5002]), ('development_seeds', [2001]), ('validation_seeds', [4001, 5002]),
                            ('validation_seeds', [5001, 5001])):
            changed = dict(plan, **{key: values})
            with self.subTest(key=key, values=values), self.assertRaises(ValueError):
                validate_fresh_seeds(changed, changed)
        with self.assertRaises(ValueError):
            validate_fresh_seeds(dict(plan, validation_seeds=[5001]), plan)

    def test_source_collision_guard_normalizes_paths(self):
        source = Path(__file__).resolve()
        with self.assertRaises(ValueError):
            guard_output_path(source.parent / '.' / source.name, [source])
        guard_output_path(source.with_name('separate_audit.json'), [source])

    def audit_map(self, values):
        payload, export, viewer, cells, policies, families = values
        return validate_map_replays(payload, export, viewer, cells, 'BASE', 'P', policies,
                                    families, 'priority_protection_validation')

    def test_three_predeclared_map_examples_match_all_fifteen_branches(self):
        values = map_fixture()
        before = deepcopy(values)
        self.assertEqual(self.audit_map(values), 15)
        self.assertEqual(values, before)

    def test_map_missing_branch_changed_outcome_or_source_input_is_rejected(self):
        for mutation in ('branch', 'outcome', 'source', 'selection'):
            values = map_fixture()
            viewer = values[2]
            if mutation == 'branch':
                viewer['scenarios'][0]['branches'].pop()
            elif mutation == 'outcome':
                viewer['scenarios'][0]['branches'][0]['patient_outcomes'][0]['treatment_start_t'] += 1
            elif mutation == 'source':
                viewer['scenarios'][0]['scenario']['inquiry_queue']['workers'] = 9
            else:
                viewer['scenarios'][0]['scenario']['experiment_selection']['seed'] = 5002
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.audit_map(values)

    def test_development_winner_is_recomputed_and_tampered_score_or_selection_fails(self):
        _, saved, _, _ = experiment_fixture()
        result = audit_development_payload(saved)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['coverage']['policies'], 8)
        for mutation in ('score', 'winner', 'coverage'):
            changed = deepcopy(saved)
            if mutation == 'score':
                changed['tuning']['priority']['scores'][0]['combined_minutes'] += 1
            elif mutation == 'winner':
                from simulation.priority_experiment import PRIORITY_POLICIES
                changed['tuning']['selected_priority_policy'] = deepcopy(PRIORITY_POLICIES[1])
                changed['tuning']['priority']['selected_policy'] = deepcopy(PRIORITY_POLICIES[1])
            else:
                changed['suites']['development']['records'].pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                audit_development_payload(changed)

    def test_complete_audit_recalculates_selection_coverage_summary_and_map(self):
        values = experiment_fixture()
        before = deepcopy(values)
        result = audit_results(*values, samples=100)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['coverage']['checked_replay_branches'], 15)
        self.assertEqual(result['coverage']['validation_branches'], 30)
        self.assertEqual(values, before)

    def test_complete_audit_rejects_changed_lock_priority_summary_and_paired_denominator(self):
        for mutation in ('lock', 'priority', 'common', 'branch_total', 'time_interval', 'inquiry_interval', 'seed_count'):
            values = list(experiment_fixture())
            complete = values[0]
            if mutation == 'lock':
                complete['tuning']['inquiry_penalty_minutes'] = 99
            elif mutation == 'priority':
                complete['suites']['validation']['overall_summary'][0]['by_priority']['4']['not_treated'] += 1
            elif mutation == 'common':
                complete['suites']['validation']['policy_comparisons'][1]['shared_patient_times']['4']['common_patient_total'] += 1
            elif mutation == 'branch_total':
                complete['branch_runs'] += 1
            elif mutation == 'time_interval':
                complete['suites']['validation']['policy_comparisons'][1]['shared_patient_times']['4']['interval_95'][0] += 1
            elif mutation == 'inquiry_interval':
                complete['suites']['validation']['policy_comparisons'][1]['inquiry_difference']['interval_95'][0] += 1
            else:
                complete['suites']['validation']['policy_comparisons'][1]['seed_count_details'][0]['priority4_not_treated_difference'] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                audit_results(*values, samples=100)


if __name__ == '__main__':
    unittest.main()
