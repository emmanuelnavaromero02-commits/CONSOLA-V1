from __future__ import annotations

import pytest

from control_room_get_harness import MutationSentinel


def test_mutation_sentinel_rejects_unapproved_select_functions():
    sentinel = MutationSentinel()

    sentinel._reject_mutation("SELECT SET_CONFIG('app.tenant_id', $1, true)")
    sentinel._reject_mutation("SELECT COUNT(*) FROM control_room_items")
    with pytest.raises(AssertionError, match="unapproved SQL function"):
        sentinel._reject_mutation("SELECT public.write_audit_event($1)")
    with pytest.raises(AssertionError, match="write_audit_event"):
        sentinel._reject_mutation(
            "SELECT SET_CONFIG('app.tenant_id', $1, true), write_audit_event()"
        )

    assert len(sentinel.mutation_attempts) == 2
