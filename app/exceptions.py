from fastapi import HTTPException


class InvalidStateTransition(Exception):
    """Raised when an invoice state transition is not allowed."""
    pass


class IdempotencyConflict(Exception):
    """Raised when an idempotency key is reused with a different request body."""
    pass


class BusinessNotFound(HTTPException):
    def __init__(self):
        super().__init__(status_code=404, detail="Business not found")


class InvoiceNotFound(HTTPException):
    def __init__(self):
        super().__init__(status_code=404, detail="Invoice not found")


class CustomerNotFound(HTTPException):
    def __init__(self):
        super().__init__(status_code=404, detail="Customer not found")