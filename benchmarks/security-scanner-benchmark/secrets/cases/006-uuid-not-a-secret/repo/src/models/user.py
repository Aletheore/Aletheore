DEFAULT_TENANT_ID = "550e8400-e29b-41d4-a716-446655440000"


class User:
    def __init__(self, tenant_id=DEFAULT_TENANT_ID):
        self.tenant_id = tenant_id
