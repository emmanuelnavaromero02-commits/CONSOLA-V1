from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import s3_client


class FakeStorage:
    def __init__(self) -> None:
        self.deleted = []

    def list_page(self, prefix, *, page_size, cursor=None, delimiter=None):
        obj = SimpleNamespace(
            key=f"{prefix}file.parquet",
            size=12,
            updated_at=datetime.now(timezone.utc),
            etag="e",
        )
        return SimpleNamespace(objects=(obj,), prefixes=(f"{prefix}folder/",), next_cursor="n", is_truncated=True)

    def presigned_get_url(self, key, *, expires_in):
        return f"https://signed/{key}?ttl={expires_in}"

    def delete_object(self, key):
        self.deleted.append(key)
        return True


def test_console_explorer_client_uses_lakehouse_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(s3_client, "storage_from_env", lambda *, bucket=None: fake)

    client = s3_client.get_lakehouse_explorer_client()
    listed = client.list_objects_v2(Bucket="lakehouse", Prefix="raw/", MaxKeys=50, Delimiter="/")

    assert listed["Contents"][0]["Key"] == "raw/file.parquet"
    assert listed["CommonPrefixes"] == [{"Prefix": "raw/folder/"}]
    assert listed["NextContinuationToken"] == "n"
    assert client.generate_presigned_url("get_object", Params={"Bucket": "lakehouse", "Key": "raw/file.parquet"}, ExpiresIn=60).startswith("https://signed/")
    assert client.delete_object(Bucket="lakehouse", Key="raw/file.parquet")["ResponseMetadata"]["HTTPStatusCode"] == 204
    assert fake.deleted == ["raw/file.parquet"]
