"""Contract: the EC2 app role can make every S3 call main makes on AWS.

Staged publication pins Silver/Gold objects by VersionId and the publication
verifier refuses to run (and fails refinement /readyz) unless it can read the
bucket versioning status. Plain s3:GetObject / s3:ListBucket grant neither, so
the inline policy must carry s3:GetObjectVersion and s3:GetBucketVersioning.
The app must still be unable to destroy a pinned version.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IAM_TF = REPO / "infra" / "terraform" / "infra" / "iam.tf"
BUCKET_ARN = "aws_s3_bucket.lakehouse.arn"
OBJECTS_ARN = '"${aws_s3_bucket.lakehouse.arn}/*"'


def _app_s3_statements() -> list[tuple[set[str], list[str]]]:
    src = IAM_TF.read_text(encoding="utf-8")
    block = re.search(
        r'data\s+"aws_iam_policy_document"\s+"app_s3"\s+\{(?P<body>[\s\S]*?)\n\}',
        src,
    )
    assert block, "missing app_s3 policy document"
    statements: list[tuple[set[str], list[str]]] = []
    for match in re.finditer(r"statement\s*\{(?P<body>[\s\S]*?)\n  \}", block["body"]):
        body = match["body"]
        actions = re.search(r"actions\s*=\s*\[(?P<items>[\s\S]*?)\]", body)
        resources = re.search(r"resources\s*=\s*\[(?P<items>[\s\S]*?)\]", body)
        assert actions and resources, "app_s3 statement without actions/resources"
        statements.append(
            (
                set(re.findall(r'"([^"]+)"', actions["items"])),
                [
                    item.strip()
                    for item in resources["items"].split(",")
                    if item.strip()
                ],
            )
        )
    return statements


def _actions_on(resource: str) -> set[str]:
    matches = [
        actions
        for actions, resources in _app_s3_statements()
        if resources == [resource]
    ]
    assert len(matches) == 1, f"expected exactly one app_s3 statement on {resource}"
    return matches[0]


def test_bucket_statement_allows_versioning_status_read() -> None:
    assert _actions_on(BUCKET_ARN) >= {
        "s3:GetBucketLocation",
        "s3:GetBucketVersioning",
        "s3:ListBucket",
    }


def test_object_statement_allows_versioned_reads() -> None:
    assert _actions_on(OBJECTS_ARN) >= {
        "s3:AbortMultipartUpload",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:ListMultipartUploadParts",
        "s3:PutObject",
    }


def test_app_role_cannot_destroy_pinned_versions_or_use_wildcards() -> None:
    statements = _app_s3_statements()
    assert len(statements) == 2
    granted = set().union(*(actions for actions, _ in statements))
    assert (
        not {
            "s3:DeleteObjectVersion",
            "s3:PutBucketVersioning",
            "s3:PutLifecycleConfiguration",
            "s3:PutBucketPolicy",
        }
        & granted
    )
    assert not any("*" in action for action in granted)
    assert all(
        resources in ([BUCKET_ARN], [OBJECTS_ARN]) for _, resources in statements
    )


def test_policy_tracks_the_code_that_needs_it() -> None:
    verifier = (
        REPO / "refinement" / "app" / "publication_verifier_worker.py"
    ).read_text(encoding="utf-8")
    storage = (REPO / "omega_lakehouse" / "s3_storage.py").read_text(encoding="utf-8")
    # If either call disappears, drop the matching permission from iam.tf.
    assert ".get_bucket_versioning(Bucket=" in verifier
    assert 'args["VersionId"] = expected_version' in storage
