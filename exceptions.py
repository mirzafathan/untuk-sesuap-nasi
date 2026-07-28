class DOMStructureChangedError(RuntimeError):
    """Raised when a career page no longer matches its expected structure."""


class JobDetailStructureChangedError(DOMStructureChangedError):
    """Raised when a job-detail response no longer matches its contract."""


class SummarizationError(RuntimeError):
    """Raised when an AI summary cannot be obtained or validated."""
