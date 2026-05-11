
class DummyClient:
    def list_tables(self):
        return [{"id": "MockEntity", "name": "Mock Entity"}]

    def get_table_schema(self, table_id):
        return {"fields": [{"name": "id", "type": "string"}]}
