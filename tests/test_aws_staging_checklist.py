from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CHECKLIST = REPO / "docs/release-evidence/aws-staging-checklist.md"


def test_aws_staging_checklist_covers_v1_public_controls():
    text = CHECKLIST.read_text(encoding="utf-8")
    for needle in (
        "Status: BLOCKED",
        "PUBLIC_CONSOLE_URL=https://",
        "PUBLIC_WORKSPACE_URL=https://",
        "E2E_LIVE_LLM=1",
        "ANTHROPIC_API_KEY",
        "OIDC",
        "production` GitHub environment",
        "AWS Secrets Manager / SSM",
        "HTTPS ALB",
        "DEPLOY_REF",
        "IMAGE_TAG",
        "make verify-v1-public",
        "backup.sh",
        "restore.sh",
        "rollback.sh <tag>",
    ):
        assert needle in text
