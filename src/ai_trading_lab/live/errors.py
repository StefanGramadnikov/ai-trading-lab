"""Failure classes with different consequences for the unattended loop."""


class LoopIntegrityError(RuntimeError):
    """Corruption, a second writer or a lost lock. Never retried: the loop stops at once."""


class CycleErrors(RuntimeError):
    """One or more strategies failed in a cycle. The others still ran; the cycle counts failed."""

    def __init__(self, detail: list[str]) -> None:
        super().__init__(f"{len(detail)} strategy error(s) in the cycle")
        self.detail = detail
