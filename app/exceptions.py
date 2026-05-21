"""
Exception hierarchy.

Domain exceptions (no FastAPI/HTTP knowledge) live here.
Routers catch them and translate to HTTP responses.
This separation keeps services testable without HTTP.
"""


class AppError(Exception):
    """Base for all domain exceptions."""
    pass


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id=None):
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource} not found" + (f": {resource_id}" if resource_id else ""))


class ConflictError(AppError):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidStateTransition(AppError):
    def __init__(self, from_state: str, to_state: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Cannot transition from '{from_state}' to '{to_state}'")


class IdempotencyConflict(AppError):
    def __init__(self):
        super().__init__("Idempotency key reused with different request body")