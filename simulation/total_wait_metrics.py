"""Whole-cohort waiting evaluation; never supplied to the allocation rule."""
from __future__ import annotations


def waiting_metrics(scenario,branch):
    end=scenario.get('max_minutes',180)
    outcome={p['id']:p for p in branch['patient_outcomes']}
    transitions={'request':'search','commit':'transit','arrive':'queue',
                 'arrival_unable':'transfer','transfer_search':'search','treatment_start':'treating'}
    histories={p['id']:[] for p in scenario['patients']}
    for event in branch['events']:
        if event['type'] in transitions and event.get('patient') in histories:
            histories[event['patient']].append((event['t'],transitions[event['type']]))
    details=[]
    for spec in sorted(scenario['patients'],key=lambda p:p['id']):
        pid=spec['id'];history=histories[pid];waits=dict(search=0,queue=0,transfer=0)
        if not history or history[0]!=(spec['search_start_t'],'search'):
            raise ValueError('whole-cohort waiting requires every initial request')
        for index,(at,state) in enumerate(history):
            stop=history[index+1][0] if index+1<len(history) else end
            if stop<at:raise ValueError('reversed waiting history')
            if state in waits:waits[state]+=stop-at
        start=outcome[pid]['treatment_start_t'];treated=start is not None
        duration=(start if treated else end)-spec['search_start_t']
        details.append(dict(patient=pid,priority=spec.get('priority',4),
            search_wait_minutes=waits['search'],queue_wait_minutes=waits['queue'],
            transfer_wait_minutes=waits['transfer'],total_wait_minutes=sum(waits.values()),
            request_to_treatment_minutes=duration if treated else None,
            request_to_treatment_capped_minutes=duration,treated=treated))
    return dict(patients=len(details),untreated=sum(not p['treated'] for p in details),
        treated_denominator=sum(p['treated'] for p in details),
        search_wait_sum_minutes=sum(p['search_wait_minutes'] for p in details),
        queue_wait_sum_minutes=sum(p['queue_wait_minutes'] for p in details),
        transfer_wait_sum_minutes=sum(p['transfer_wait_minutes'] for p in details),
        total_wait_sum_minutes=sum(p['total_wait_minutes'] for p in details),
        priority_weighted_total_wait_sum_minutes=sum(p['priority']*p['total_wait_minutes'] for p in details),
        max_patient_wait_minutes=max((p['total_wait_minutes'] for p in details),default=0),
        request_to_treatment_sum_minutes=sum(p['request_to_treatment_minutes'] for p in details if p['treated']),
        request_to_treatment_capped_sum_minutes=sum(p['request_to_treatment_capped_minutes'] for p in details),
        priority_weighted_request_to_treatment_capped_sum_minutes=sum(p['priority']*p['request_to_treatment_capped_minutes'] for p in details),
        patient_details=details)
