"""T2e — el Postgres de AWS deja de vivir sin respaldos en el IaC.

La auditoría (alta, confirmada): toda la base (Postgres en contenedores)
vive en el disco raíz del único EC2 con delete_on_termination=true, sin
aws_backup_plan, sin snapshots y sin cron de backup (el cron diario solo se
instalaba en GCP). Una terminación —error humano o terraform destroy—
destruía la base entera sin copia.

El arreglo espejo del host GCP (F4): cron.d versionado en el repo instalado
por el user_data, backup.sh con backend PINNEADO a s3 (destino
s3://$S3_BUCKET_NAME/backups/), y candado disable_api_termination en la
instancia. Los hosts ya aprovisionados requieren la instalación única del
cron por el dueño (documentada en el propio archivo).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TF = REPO_ROOT / "infra" / "terraform"


def test_versioned_aws_backup_cron():
    cron = (TF / "files" / "omega-backup-aws.cron").read_text(encoding="utf-8")
    assert "BACKUP_STORAGE_BACKEND=s3" in cron, "backend pinneado: jamás desviable"
    assert "/etc/modecissions/aws-entrypoint.env" in cron, "S3_BUCKET_NAME/AWS_REGION del host"
    assert "infra/terraform/deploy/backup.sh" in cron
    assert "docker-compose.aws.yml" in cron
    assert "10 3 * * * root" in cron, "03:10 UTC diario, como el host GCP"


def test_user_data_installs_the_cron_before_ready():
    tpl = (TF / "infra" / "user_data" / "app.sh.tpl").read_text(encoding="utf-8")
    assert "omega-backup-aws.cron /etc/cron.d/omega-backup" in tpl
    assert tpl.index("omega-backup-aws.cron") < tpl.index(
        "date -Iseconds > /opt/modecissions/READY"
    ), "el cron queda instalado antes del marcador READY"


def test_app_instance_has_termination_lock():
    tf = (TF / "infra" / "ec2_app.tf").read_text(encoding="utf-8")
    assert "disable_api_termination = true" in tf
    assert tf.index("disable_api_termination") < tf.index("root_block_device"), (
        "el candado vive en el resource de la instancia app"
    )


def test_backup_script_s3_backend_contract():
    sh = (TF / "deploy" / "backup.sh").read_text(encoding="utf-8")
    assert 'BACKUP_DESTINATION="s3://${S3_BUCKET_NAME}/backups/"' in sh
    assert "S3_BUCKET_NAME is required" in sh, "fail-closed sin bucket"
