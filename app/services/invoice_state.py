"""
Invoice state machine — pure functions, no DB, no FastAPI.

This is the single source of truth for what transitions are allowed.
Testable without any infrastructure.
"""
from app.models.invoice import InvoiceState, TERMINAL_STATES
from app.exceptions import InvalidStateTransition

# Every valid transition: from_state -> set of allowed to_states
ALLOWED_TRANSITIONS: dict[InvoiceState, set[InvoiceState]] = {
    InvoiceState.DRAFT: {InvoiceState.OPEN},
    InvoiceState.OPEN: {
        InvoiceState.PAID,
        InvoiceState.VOID,
        InvoiceState.UNCOLLECTIBLE,
    },
    InvoiceState.PAID: set(),
    InvoiceState.VOID: set(),
    InvoiceState.UNCOLLECTIBLE: set(),
}


def assert_transition_allowed(current_state: str, target_state: InvoiceState) -> None:
    """
    Raise InvalidStateTransition if the transition is not allowed.
    Called by the service before mutating state.
    """
    current = InvoiceState(current_state)

    if current in TERMINAL_STATES:
        raise InvalidStateTransition(
            current_state,
            target_state.value,
            reason=f"invoice is in terminal state '{current_state}'",
        )

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target_state not in allowed:
        raise InvalidStateTransition(current_state, target_state.value)