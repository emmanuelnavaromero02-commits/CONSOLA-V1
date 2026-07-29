from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_authority_audit import (
    authority_audit as _authority_audit,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationConflict,
    acquire_guarded_action_reservation,
)
from app.services.control_room.business_action_resolution import (
    authorized_explicit_action_bindings,
)
from app.services.control_room.business_execution_approval import (
    require_approved_execution,
)
from app.services.control_room.business_execution_precondition import (
    lock_pending_action_reservation,
)
from app.services.control_room.business_explicit_action_binding import (
    VerifiedActionBinding,
)
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)


Clock = Callable[[], datetime]
ItemBuilder = Callable[[Mapping[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class AuthoritativeExecutionContext:
    item: dict[str, Any]
    template: dict[str, Any]
    payload: dict[str, Any]
    binding: VerifiedActionBinding
    authority_audit: dict[str, Any]


@dataclass(frozen=True)
class AuthoritativeActionReservation:
    context: AuthoritativeExecutionContext
    reservation: ActionReservation


def _changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "item_business_state_changed",
            "message": "control room item changed; reload before mutating",
        },
    )


def _clock_snapshot(clock: Clock | None) -> Clock:
    value = clock() if clock is not None else datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _changed()
    instant = value.astimezone(UTC)
    return lambda: instant


def _required_binding(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    template_id: str,
    binding_id: str,
    clock: Clock,
) -> VerifiedActionBinding:
    binding = next(
        (
            candidate
            for candidate in authorized_explicit_action_bindings(
                item, user, clock=clock
            )
            if candidate.template_id == template_id
            and candidate.binding_id == binding_id
        ),
        None,
    )
    if binding is None:
        raise _changed()
    return binding


def _build_context(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    template_id: str,
    binding_id: str,
    payload_builder: Any,
    clock: Clock,
) -> AuthoritativeExecutionContext:
    binding = _required_binding(
        item,
        user,
        template_id=template_id,
        binding_id=binding_id,
        clock=clock,
    )
    template = ACTION_TEMPLATES.get(template_id)
    if template is None:
        raise _changed()
    try:
        payload = payload_builder(dict(item), dict(template))
    except (KeyError, TypeError, ValueError):
        raise _changed() from None
    if not isinstance(payload, Mapping):
        raise _changed()
    payload_dict = dict(payload)
    return AuthoritativeExecutionContext(
        item=item if isinstance(item, dict) else dict(item),
        template=dict(template),
        payload=payload_dict,
        binding=binding,
        authority_audit=_authority_audit(binding, payload_dict),
    )


async def lock_authoritative_execution_context(
    conn: Any,
    *,
    user: Mapping[str, Any],
    expected_item: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
    template_id: str,
    binding_id: str,
    item_builder: ItemBuilder,
    payload_builder: Any,
    clock: Clock | None = None,
) -> AuthoritativeExecutionContext:
    locked = await lock_authoritative_business_item(
        conn,
        user=user,
        item=expected_item,
        decision_id=int(expected_item.get("decision_id") or 0) or None,
    )
    fixed_clock = _clock_snapshot(clock)
    if locked is None:
        raise _changed()
    authoritative = item_builder(locked)
    if authoritative is None:
        raise _changed()
    expected_binding = _required_binding(
        expected_item,
        user,
        template_id=template_id,
        binding_id=binding_id,
        clock=fixed_clock,
    )
    expected_authority = _authority_audit(expected_binding, expected_payload)
    context = _build_context(
        authoritative,
        user,
        template_id=template_id,
        binding_id=binding_id,
        payload_builder=payload_builder,
        clock=fixed_clock,
    )
    if context.authority_audit != expected_authority:
        raise _changed()
    return context


async def acquire_authoritative_action_reservation(
    conn: Any,
    *,
    user: Mapping[str, Any],
    expected_item: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
    template_id: str,
    binding_id: str,
    adapter_name: str,
    operation: str,
    provided_key: str | None,
    item_builder: ItemBuilder,
    payload_builder: Any,
    clock: Clock | None = None,
) -> AuthoritativeActionReservation:
    from app.services.control_room.business_authoritative_replay import (
        authoritative_context_or_completed_replay,
    )

    context, completed = await authoritative_context_or_completed_replay(
        globals(),
        conn,
        user=user,
        expected_item=expected_item,
        expected_payload=expected_payload,
        template_id=template_id,
        binding_id=binding_id,
        item_builder=item_builder,
        payload_builder=payload_builder,
        clock=clock,
    )
    if completed is not None:
        return AuthoritativeActionReservation(context, completed)
    try:
        reservation = await acquire_guarded_action_reservation(
            conn,
            user=user,
            item=context.item,
            template_id=template_id,
            adapter_name=adapter_name,
            operation=operation,
            provided_key=provided_key,
            input_payload=context.payload,
            authority_audit=context.authority_audit,
            reservation_guard=lambda: require_authoritative_binding_current(
                context,
                user,
                clock=clock,
            ),
        )
    except ReservationConflict:
        raise _changed() from None
    return AuthoritativeActionReservation(context=context, reservation=reservation)


async def revalidate_authoritative_action(
    conn: Any,
    *,
    user: Mapping[str, Any],
    expected: AuthoritativeExecutionContext,
    reservation: ActionReservation,
    item_builder: ItemBuilder,
    payload_builder: Any,
    clock: Clock | None = None,
) -> tuple[AuthoritativeExecutionContext, dict[str, Any]]:
    context = await lock_authoritative_execution_context(
        conn,
        user=user,
        expected_item=expected.item,
        expected_payload=expected.payload,
        template_id=expected.binding.template_id,
        binding_id=expected.binding.binding_id,
        item_builder=item_builder,
        payload_builder=payload_builder,
        clock=clock,
    )
    await require_approved_execution(
        conn,
        user=user,
        item=context.item,
        template_id=context.binding.template_id,
    )
    locked_reservation = await lock_pending_action_reservation(
        conn,
        user=user,
        item=context.item,
        template_id=context.binding.template_id,
        reservation_id=reservation.id,
        effective_key=reservation.effective_key,
        input_payload=context.payload,
        authority_audit=context.authority_audit,
        lease_token=reservation.lease_token,
    )
    return context, locked_reservation


def require_authoritative_binding_current(
    context: AuthoritativeExecutionContext,
    user: Mapping[str, Any],
    clock: Clock | None = None,
) -> None:
    fixed_clock = _clock_snapshot(clock)
    binding = _required_binding(
        context.item,
        user,
        template_id=context.binding.template_id,
        binding_id=context.binding.binding_id,
        clock=fixed_clock,
    )
    if _authority_audit(binding, context.payload) != context.authority_audit:
        raise _changed()
