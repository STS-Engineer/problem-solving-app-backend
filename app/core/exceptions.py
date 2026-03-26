class InvalidStepCodeError(Exception):
    """Raised when step code is invalid."""


class ComplaintNotFoundError(Exception):
    """Raised when complaint is not found."""


class ReportNotFoundError(Exception):
    """Raised when report is not found."""


class StepNotFoundError(Exception):
    """Raised when step is not found."""