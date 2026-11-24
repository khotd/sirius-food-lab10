class GatewayError(Exception):
    """Безопасный код ошибки шлюза; не содержит секретов и реквизитов."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
