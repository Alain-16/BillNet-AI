class DomainError(Exception):
    """A business-rule violation. Never used for programmer errors or bugs."""

    def __init__(self, message: str, *, code: str = "domain_error", field: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field 