"""Deterministic, hospital-local FIFO inquiry processing for experiments.

Each submitted inquiry occupies one of a hospital's ``workers`` for an integer
number of minutes. Service is non-preemptive. Jobs are never cancelled, including
when another hospital accepts the patient. A ``silent`` job consumes the same
work but its eventual reply is omitted by the caller. These are experimental
assumptions, not estimates of real hospital staffing or behaviour.

``jobs`` contains complete scheduling records for the experiment runner. Do not
give those records to an allocation policy: start/finish times are future facts.
Use ``snapshot(t)`` for observations at a particular time. Its accumulated wait
and service are *elapsed person-minutes through t*, not the full scheduled work
of unfinished jobs. It exposes no future start or completion times.
"""
from collections.abc import Iterable


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


class InquiryQueue:
    """Independent FCFS queues with a fixed worker count at every hospital.

    ``enqueue`` must be called in submission order within each hospital. Equal
    submission times preserve call order; jobs at different hospitals are
    independent. A service ending at t frees its worker for a new job at t.
    """

    def __init__(self, hospital_ids: Iterable, workers=1):
        self.workers = _integer(workers, 'workers', 1)
        self.hospital_ids = list(hospital_ids)
        if len(set(self.hospital_ids)) != len(self.hospital_ids):
            raise ValueError('hospital_ids must be unique')
        self.jobs = []
        self._jobs_by_hospital = {hospital: [] for hospital in self.hospital_ids}
        self._available = {hospital: [0] * self.workers for hospital in self.hospital_ids}
        self._last_submission = {hospital: -1 for hospital in self.hospital_ids}

    def enqueue(self, hospital, t, patient, attempt, service_minutes,
                kind='inquiry', silent=False):
        """Schedule one job; only the experiment runner may read its times."""
        if hospital not in self._available:
            raise ValueError(f'unknown hospital: {hospital}')
        t = _integer(t, 't')
        service_minutes = _integer(service_minutes, 'service_minutes', 1)
        if t < self._last_submission[hospital]:
            raise ValueError('submissions must be chronological within a hospital')
        if not isinstance(silent, bool):
            raise ValueError('silent must be a bool')

        available = self._available[hospital]
        # Tie-breaking by worker index is deterministic. A single global queue
        # is represented by assigning the earliest available worker each time.
        worker = min(range(self.workers), key=lambda index: (available[index], index))
        start = max(t, available[worker])
        finish = start + service_minutes
        job = dict(hospital=hospital, patient=patient, attempt=attempt,
                   submitted_t=t, start_t=start, finish_t=finish,
                   service_minutes=service_minutes,
                   queue_wait_minutes=start - t, kind=kind, silent=silent)
        available[worker] = finish
        self._last_submission[hospital] = t
        self.jobs.append(job)
        self._jobs_by_hospital[hospital].append(job)
        # Avoid callers accidentally mutating the queue's own scheduling record.
        return dict(job)

    def snapshot(self, t):
        """Return current counts and elapsed work; do not reveal future times.

        waiting: submitted <= t < start; processing: start <= t < finish;
        completed: finish <= t. Wait/service totals include unfinished jobs only
        to the extent that their waiting/service has actually elapsed by t.
        Jobs submitted after t are completely excluded, allowing replay queries.
        """
        t = _integer(t, 't')
        result = []
        for hospital in self.hospital_ids:
            jobs = [job for job in self._jobs_by_hospital[hospital]
                    if job['submitted_t'] <= t]
            row = dict(hospital=hospital, workers=self.workers, waiting=0,
                       processing=0, completed=0, submitted=len(jobs),
                       queue_wait_total_minutes=0, service_total_minutes=0)
            for job in jobs:
                if job['finish_t'] <= t:
                    row['completed'] += 1
                elif job['start_t'] <= t:
                    row['processing'] += 1
                else:
                    row['waiting'] += 1
                row['queue_wait_total_minutes'] += max(
                    0, min(t, job['start_t']) - job['submitted_t'])
                row['service_total_minutes'] += max(
                    0, min(t, job['finish_t']) - job['start_t'])
            result.append(row)
        return result

    def report(self, end_t):
        """Aggregate the same bounded observations as ``snapshot(end_t)``."""
        rows = self.snapshot(end_t)
        fields = ('workers', 'waiting', 'processing', 'completed', 'submitted',
                  'queue_wait_total_minutes', 'service_total_minutes')
        aggregate = {field: sum(row[field] for row in rows) for field in fields}
        return dict(end_t=end_t, aggregate=aggregate, by_hospital=rows)
