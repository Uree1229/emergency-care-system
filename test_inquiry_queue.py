"""Meaningful scheduling and observation boundaries for inquiry workloads."""
import unittest

from simulation.inquiry_queue import InquiryQueue


class InquiryQueueTests(unittest.TestCase):
    def test_one_worker_serves_in_call_order_even_at_the_same_time(self):
        queue = InquiryQueue(['H'])
        first = queue.enqueue('H', 2, 'A', 1, 3)
        second = queue.enqueue('H', 2, 'B', 1, 1)
        third = queue.enqueue('H', 3, 'C', 1, 2)
        self.assertEqual((first['start_t'], first['finish_t']), (2, 5))
        self.assertEqual((second['start_t'], second['finish_t']), (5, 6))
        self.assertEqual((third['start_t'], third['finish_t']), (6, 8))
        self.assertEqual([job['queue_wait_minutes'] for job in queue.jobs], [0, 3, 3])

    def test_multiple_workers_begin_concurrently_then_earliest_free_is_used(self):
        queue = InquiryQueue(['H'], workers=2)
        records = [queue.enqueue('H', 0, patient, 1, duration)
                   for patient, duration in [('A', 5), ('B', 2), ('C', 3), ('D', 1)]]
        self.assertEqual([row['start_t'] for row in records], [0, 0, 2, 5])
        self.assertEqual(queue.snapshot(1)[0]['processing'], 2)
        self.assertEqual(queue.snapshot(1)[0]['waiting'], 2)
        self.assertEqual(queue.snapshot(5)[0]['processing'], 1)

    def test_hospitals_have_independent_queues(self):
        queue = InquiryQueue(['H1', 'H2'])
        queue.enqueue('H1', 0, 'A', 1, 8)
        second = queue.enqueue('H2', 1, 'B', 1, 2)
        self.assertEqual((second['start_t'], second['finish_t']), (1, 3))
        rows = {row['hospital']: row for row in queue.snapshot(3)}
        self.assertEqual(rows['H1']['processing'], 1)
        self.assertEqual(rows['H2']['completed'], 1)

    def test_exact_time_boundaries_and_elapsed_work(self):
        queue = InquiryQueue(['H'])
        queue.enqueue('H', 2, 'A', 1, 3)
        queue.enqueue('H', 3, 'B', 1, 2)
        self.assertEqual(queue.snapshot(1)[0]['submitted'], 0)
        start = queue.snapshot(2)[0]
        self.assertEqual((start['processing'], start['waiting'], start['completed']), (1, 0, 0))
        self.assertEqual(start['service_total_minutes'], 0)
        during = queue.snapshot(4)[0]
        self.assertEqual((during['processing'], during['waiting'], during['completed']), (1, 1, 0))
        self.assertEqual((during['queue_wait_total_minutes'], during['service_total_minutes']), (1, 2))
        transition = queue.snapshot(5)[0]
        self.assertEqual((transition['processing'], transition['waiting'], transition['completed']), (1, 0, 1))
        self.assertEqual((transition['queue_wait_total_minutes'], transition['service_total_minutes']), (2, 3))
        end = queue.snapshot(7)[0]
        self.assertEqual((end['processing'], end['waiting'], end['completed']), (0, 0, 2))
        self.assertEqual((end['queue_wait_total_minutes'], end['service_total_minutes']), (2, 5))

    def test_snapshot_hides_future_submissions_and_scheduled_times(self):
        queue = InquiryQueue(['H'])
        queue.enqueue('H', 0, 'A', 1, 5)
        queue.enqueue('H', 0, 'B', 1, 100)
        queue.enqueue('H', 20, 'FUTURE', 1, 2)
        observation = queue.snapshot(1)[0]
        self.assertEqual(observation['submitted'], 2)
        self.assertEqual(observation['completed'], 0)
        self.assertEqual(observation['waiting'], 1)
        self.assertEqual(observation['service_total_minutes'], 1)
        self.assertEqual(observation['queue_wait_total_minutes'], 1)
        self.assertFalse({'start_t', 'finish_t', 'jobs'} & observation.keys())

    def test_silent_jobs_still_consume_processing_capacity(self):
        queue = InquiryQueue(['H'])
        silent = queue.enqueue('H', 0, 'A', 1, 5, silent=True)
        normal = queue.enqueue('H', 1, 'B', 1, 1)
        self.assertTrue(silent['silent'])
        self.assertEqual(normal['start_t'], 5)
        self.assertEqual(queue.snapshot(5)[0]['completed'], 1)

    def test_requests_keep_processing_without_cancellation(self):
        queue = InquiryQueue(['H'])
        queue.enqueue('H', 0, 'A', 1, 2, kind='inquiry')
        queue.enqueue('H', 0, 'A', 2, 2, kind='recheck')
        # Acceptance elsewhere does not remove scheduled work from this queue.
        row = queue.snapshot(3)[0]
        self.assertEqual((row['submitted'], row['completed'], row['processing']), (2, 1, 1))
        self.assertEqual(queue.jobs[1]['kind'], 'recheck')

    def test_report_is_sum_of_hospital_observations(self):
        queue = InquiryQueue(['H1', 'H2'], workers=2)
        queue.enqueue('H1', 0, 'A', 1, 2)
        queue.enqueue('H2', 1, 'B', 1, 3)
        report = queue.report(3)
        self.assertEqual(report['end_t'], 3)
        self.assertEqual(report['aggregate']['workers'], 4)
        self.assertEqual(report['aggregate']['submitted'], 2)
        self.assertEqual(report['aggregate']['completed'], 1)
        self.assertEqual(report['aggregate']['processing'], 1)
        self.assertEqual(report['aggregate']['service_total_minutes'], 4)
        self.assertEqual(report['by_hospital'], queue.snapshot(3))

    def test_invalid_values_and_unknown_hospitals_are_rejected(self):
        for workers in (0, -1, 1.5, True):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                InquiryQueue(['H'], workers=workers)
        with self.assertRaises(ValueError):
            InquiryQueue(['H', 'H'])
        queue = InquiryQueue(['H'])
        for t in (-1, 1.5, True):
            with self.subTest(t=t), self.assertRaises(ValueError):
                queue.enqueue('H', t, 'A', 1, 1)
        for service in (0, -1, 1.5, False):
            with self.subTest(service=service), self.assertRaises(ValueError):
                queue.enqueue('H', 0, 'A', 1, service)
        with self.assertRaises(ValueError):
            queue.enqueue('UNKNOWN', 0, 'A', 1, 1)
        with self.assertRaises(ValueError):
            queue.snapshot(-1)
        with self.assertRaises(ValueError):
            queue.enqueue('H', 0, 'A', 1, 1, silent='yes')

    def test_out_of_order_submissions_rejected_only_within_same_hospital(self):
        queue = InquiryQueue(['H1', 'H2'])
        queue.enqueue('H1', 5, 'A', 1, 1)
        queue.enqueue('H2', 1, 'B', 1, 1)
        with self.assertRaises(ValueError):
            queue.enqueue('H1', 4, 'C', 1, 1)

    def test_mutating_returned_job_does_not_change_queue(self):
        queue = InquiryQueue(['H'])
        job = queue.enqueue('H', 0, 'A', 1, 2)
        job['finish_t'] = 0
        self.assertEqual(queue.snapshot(1)[0]['completed'], 0)

    def test_reproducible_scheduling_and_empty_report(self):
        queues = [InquiryQueue(['H'], workers=2) for _ in range(2)]
        for queue in queues:
            for patient, t, duration in [('A', 0, 3), ('B', 0, 3), ('C', 1, 2)]:
                queue.enqueue('H', t, patient, 1, duration)
        self.assertEqual(queues[0].jobs, queues[1].jobs)
        self.assertEqual(InquiryQueue([]).report(0)['aggregate']['submitted'], 0)


if __name__ == '__main__':
    unittest.main()
