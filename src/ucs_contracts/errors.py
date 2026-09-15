"""Stable validation errors exposed by the contracts package."""


class ContractValidationError(ValueError):
    """Raised when data does not satisfy a UCS interface contract."""
