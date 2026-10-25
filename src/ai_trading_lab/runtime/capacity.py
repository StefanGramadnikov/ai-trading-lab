"""Explicit engineering ceilings and canonical-byte admission accounting."""

MAX_JOURNAL_BYTES = 512 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024
TERMINAL_RESERVE_BYTES = 32 * 1024
CREATION_RESERVE_BYTES = 16 * 1024
POLL_ENVELOPE_BYTES = 1024
MIN_EVENT_BYTES = 1024


def cycle_overhead() -> int:
    """Each cycle may persist both a poll intent and a result checkpoint."""
    return 2 * MAX_CHECKPOINT_BYTES + POLL_ENVELOPE_BYTES
