from pathlib import Path


def test_replicon_packaged_sql_uses_bucket_placeholder():
    offenders = []
    for path in Path("cartridges/replicon/datasets").glob("*.sql"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("modecissions-lakehouse", "s3://lakehouse/"):
            if forbidden in text:
                offenders.append(f"{path}:{forbidden}")

    assert not offenders, "Replicon SQL must use the {bucket} placeholder: " + ", ".join(offenders)
