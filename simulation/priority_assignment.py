"""Same-minute joint assignment with explicit experimental priority tiers.

This rule consumes only patients already ready for assignment and their current
valid hospital options. Resource availability is the same current snapshot used
by R3; future patients, scheduled resource releases and reply schedules are not
read. Assignments already committed by the caller are neither revoked nor
changed. Priority values 4, 2 and 1 are experiment labels, not clinical criteria.
"""
from collections import Counter
from itertools import product
import math


PRIORITY_TIERS = (4, 2, 1)


class PriorityJointAssignmentRule:
    """Lexicographic priority protection among this minute's valid offers.

    Minimize unassigned counts for tiers 4, 2, 1, then estimated travel sums
    for tiers 4, 2, 1. This can prefer one tier-4 patient to two tier-2 patients;
    protecting the highest tier does not promise maximum total assignments.

    Exact enumeration is bounded by ``limit`` combinations. Larger batches
    use a deterministic, priority-ordered nearest feasible greedy fallback.
    The fallback preserves capacities and tier ordering but is not an exact
    solution to the joint objective, particularly with scarce alternatives.
    """

    code = 'R4'
    joint = True

    def __init__(self, limit=20000):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive integer')
        self.limit = limit
        self.fallbacks = 0

    def choose(self, sim, p, options, t):
        """Single-patient/redirect choice among current valid options only."""
        loc = sim.position(p, t)
        return min(options, key=lambda hid: (sim.travel(p, hid, loc), hid))

    def decide_batch(self, sim, ready, t):
        """Return patient ID -> hospital ID/None without mutating the world."""
        ordered = sorted(((p, sorted(set(options))) for p, options in ready),
                         key=lambda row: (-row[0].priority, row[0].t_req,
                                          row[0].id))
        if not ordered:
            return {}
        ids = [p.id for p, _ in ordered]
        if len(set(ids)) != len(ids):
            raise ValueError('ready patients must have unique IDs')
        if any(p.priority not in PRIORITY_TIERS for p, _ in ordered):
            raise ValueError('ready patient priority must be 4, 2, or 1')

        if math.prod(len(options) + 1 for _, options in ordered) > self.limit:
            self.fallbacks += 1
            return self._greedy(sim, ordered, t)

        offered = sorted({hid for _, options in ordered for hid in options})
        free = {(hid, resource): sim.hospitals[hid].free(resource)
                for hid in offered for resource in sim.hospitals[hid].cap}
        travel = {(p.id, hid): sim.travel(p, hid, sim.position(p, t))
                  for p, options in ordered for hid in options}
        best, best_key = None, None
        for combo in product(*(options + [None] for _, options in ordered)):
            use = Counter()
            for (p, _), hid in zip(ordered, combo):
                if hid is not None:
                    for resource, quantity in p.demand.items():
                        use[(hid, resource)] += quantity
            if any(quantity > free.get(key, 0) for key, quantity in use.items()):
                continue
            key = tuple(sum(p.priority == tier and hid is None
                            for (p, _), hid in zip(ordered, combo))
                        for tier in PRIORITY_TIERS)
            key += tuple(sum(travel[(p.id, hid)]
                             for (p, _), hid in zip(ordered, combo)
                             if p.priority == tier and hid is not None)
                         for tier in PRIORITY_TIERS)
            if best_key is None or key < best_key:
                best, best_key = combo, key
        # The all-unassigned combination is always feasible, even when an
        # existing commitment makes a resource snapshot negative.
        return {p.id: hid for (p, _), hid in zip(ordered, best)}

    def _greedy(self, sim, ordered, t):
        assignments, taken = {}, Counter()
        for p, options in ordered:
            feasible = [hid for hid in options
                        if all(sim.hospitals[hid].free(resource)
                               - taken[(hid, resource)] >= quantity
                               for resource, quantity in p.demand.items())]
            hid = self.choose(sim, p, feasible, t) if feasible else None
            assignments[p.id] = hid
            if hid is not None:
                for resource, quantity in p.demand.items():
                    taken[(hid, resource)] += quantity
        return assignments
