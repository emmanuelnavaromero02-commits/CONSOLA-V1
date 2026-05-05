from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable
from datetime import datetime, timedelta, timezone
import imaplib
import email
import zipfile
import io
import os
import uuid
from minio import Minio
from minio.commonconfig import CopySource
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

default_args = {
    'owner': 'replicon',
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    dag_id='replicon_outlook_audit_report_import',
    default_args=default_args,
    description='Descarga CSV desde Outlook → MinIO (auditoría) → Parquet (Bronze)',
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    tags=['replicon', 'outlook', 'audit', 'ingest', 'bronze'],
    catchup=False,
)

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

OUTLOOK_RECEIVER_EMAIL = Variable.get("outlook_receiver_email")
OUTLOOK_SENDER_EMAIL = Variable.get("outlook_sender_email")
OUTLOOK_PASSWORD = Variable.get("outlook_app_password")
OUTLOOK_SUBJECT = "VO_PRD_KPI_PROJECT_PROGRESS_AUDIT from Replicon"
ZIP_FILENAME = "VO_PRD_KPI_PROJECT_AUDIT Daily.zip"
CSV_FILENAME = "VO_PRD_KPI_PROJECT_AUDIT Daily.csv"

MINIO_ENDPOINT   = Variable.get("minio_endpoint")
MINIO_ACCESS_KEY = Variable.get("minio_access_key")
MINIO_SECRET_KEY = Variable.get("minio_secret_key")
MINIO_BUCKET     = Variable.get("minio_bucket")
MINIO_SECURE     = Variable.get("minio_secure", default_var="false").lower() == "true"
MINIO_UPLOAD_PATH = "uploads/replicon/in"
MINIO_BAK_PATH    = "uploads/replicon/bak"

CARTRIDGE_ID = "replicon"
ENTITY = "ProjectAudit"

def _minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )

def _ensure_bucket():
    client = _minio_client()
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)
        print(f"✓ Bucket creado: {MINIO_BUCKET}")

# ============================================================================
# TASK 1: Descargar ZIP desde Outlook y extraer CSV
# ============================================================================

def fetch_outlook_attachment(**context):
    print(f"🔍 Conectando a Outlook...")
    
    mail = imaplib.IMAP4_SSL('imap-mail.outlook.com', 993)
    mail.login(OUTLOOK_RECEIVER_EMAIL, OUTLOOK_PASSWORD)
    mail.select('INBOX')
    
    status, messages = mail.search(None, f'(UNSEEN FROM "{OUTLOOK_SENDER_EMAIL}")')
    
    if not messages or not messages[0]:
        raise ValueError(f"No hay correos no leídos de {OUTLOOK_SENDER_EMAIL}")
    
    email_ids = messages[0].split()
    latest_email_id = email_ids[-1]
    
    status, msg_data = mail.fetch(latest_email_id, '(RFC822)')
    msg = email.message_from_bytes(msg_data[0][1])
    
    if OUTLOOK_SUBJECT not in msg['Subject']:
        raise ValueError(f"Asunto no coincide. Esperaba: {OUTLOOK_SUBJECT}")
    
    print(f"✓ Correo encontrado: {msg['Subject']}")
    print(f"  De: {msg['From']}")
    print(f"  Fecha: {msg['Date']}")
    
    # Extraer ZIP
    zip_content = None
    for part in msg.walk():
        if part.get_content_disposition() == 'attachment':
            filename = part.get_filename()
            if filename and ZIP_FILENAME in filename:
                zip_content = part.get_payload(decode=True)
                print(f"✓ ZIP descargado: {filename} ({len(zip_content)} bytes)")
                break
    
    if not zip_content:
        raise ValueError(f"No se encontró attachment: {ZIP_FILENAME}")
    
    # Extraer CSV del ZIP
    csv_content = None
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        for filename in zf.namelist():
            if CSV_FILENAME in filename:
                csv_content = zf.read(filename)
                print(f"✓ CSV extraído: {filename} ({len(csv_content)} bytes)")
                break
    
    if not csv_content:
        raise ValueError(f"No se encontró CSV dentro del ZIP: {CSV_FILENAME}")
    
    # Guardar CSV en tmp
    tmp_csv_path = f"/tmp/{CSV_FILENAME}"
    with open(tmp_csv_path, 'wb') as f:
        f.write(csv_content)
    
    # Marcar correo como leído
    mail.store(latest_email_id, '+FLAGS', '\\\\Seen')
    mail.close()
    mail.logout()
    
    context['task_instance'].xcom_push(key='csv_path', value=tmp_csv_path)
    context['task_instance'].xcom_push(key='csv_filename', value=CSV_FILENAME)
    context['task_instance'].xcom_push(key='csv_bytes', value=len(csv_content))
    
    return {'status': 'success', 'csv_file': CSV_FILENAME, 'csv_bytes': len(csv_content)}

# ============================================================================
# TASK 2: Depositar CSV en MinIO para auditoría/trazabilidad
# ============================================================================

def upload_csv_to_minio(**context):
    print(f"📤 Depositando CSV en MinIO para auditoría...")
    
    csv_path = context['task_instance'].xcom_pull(task_ids='fetch_outlook_attachment', key='csv_path')
    csv_filename = context['task_instance'].xcom_pull(task_ids='fetch_outlook_attachment', key='csv_filename')
    csv_bytes = context['task_instance'].xcom_pull(task_ids='fetch_outlook_attachment', key='csv_bytes')
    
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV no encontrado: {csv_path}")
    
    _ensure_bucket()
    client = _minio_client()
    
    minio_csv_path = f"{MINIO_UPLOAD_PATH}/{csv_filename}"
    file_size = os.path.getsize(csv_path)
    
    with open(csv_path, 'rb') as f:
        client.put_object(MINIO_BUCKET, minio_csv_path, f, file_size)
    
    print(f"✓ CSV depositado en MinIO para auditoría")
    print(f"  Ruta: s3://{MINIO_BUCKET}/{minio_csv_path}")
    
    context['task_instance'].xcom_push(key='minio_csv_uri', value=f"s3://{MINIO_BUCKET}/{minio_csv_path}")
    
    return {'status': 'success', 'minio_csv_uri': f"s3://{MINIO_BUCKET}/{minio_csv_path}"}

# ============================================================================
# TASK 3: Convertir CSV a Parquet y guardar en Bronze
# ============================================================================

def csv_to_parquet(**context):
    print(f"🔄 Convirtiendo CSV a Parquet...")
    
    csv_path = context['task_instance'].xcom_pull(task_ids='fetch_outlook_attachment', key='csv_path')
    minio_csv_uri = context['task_instance'].xcom_pull(task_ids='upload_csv_to_minio', key='minio_csv_uri')
    
    batch_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    
    # Leer CSV y convertir a DataFrame
    df = pd.read_csv(csv_path, sep=",", encoding="utf-8", header=0)
    row_count = len(df)
    
    print(f"✓ CSV leído: {row_count} filas")
    
    # Convertir a Parquet
    _ensure_bucket()
    client = _minio_client()
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parquet_prefix = f"raw/{CARTRIDGE_ID}/{ENTITY}/load_date={today}/"
    parquet_key = f"{parquet_prefix}data.parquet"
    
    # Eliminar parquets previos del mismo día
    for obj in client.list_objects(MINIO_BUCKET, prefix=parquet_prefix, recursive=True):
        client.remove_object(MINIO_BUCKET, obj.object_name)
        print(f"✓ Eliminado parquet anterior: {obj.object_name}")
    
    # Escribir Parquet
    table = pa.Table.from_pandas(df)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    bytes_written = buf.getbuffer().nbytes
    
    client.put_object(MINIO_BUCKET, parquet_key, buf, bytes_written, content_type="application/octet-stream")
    
    parquet_uri = f"s3://{MINIO_BUCKET}/{parquet_key}"
    print(f"✓ Parquet creado en Bronze")
    print(f"  Ruta: {parquet_uri}")
    print(f"  Tamaño: {bytes_written} bytes")
    
    context['task_instance'].xcom_push(key='batch_id', value=batch_id)
    context['task_instance'].xcom_push(key='parquet_uri', value=parquet_uri)
    context['task_instance'].xcom_push(key='row_count', value=row_count)
    context['task_instance'].xcom_push(key='bytes_written', value=bytes_written)
    context['task_instance'].xcom_push(key='started_at', value=started_at)
    context['task_instance'].xcom_push(key='finished_at', value=datetime.now(timezone.utc).isoformat())
    
    return {
        'status': 'success',
        'batch_id': batch_id,
        'row_count': row_count,
        'parquet_uri': parquet_uri,
        'bytes_written': bytes_written
    }

# ============================================================================
# TASK 4: Mover CSV a Backup después del procesamiento (para auditoría)
# ============================================================================

def move_csv_to_backup(**context):
    print(f"📦 Moviendo CSV a backup para auditoría...")
    
    minio_csv_uri = context['task_instance'].xcom_pull(task_ids='upload_csv_to_minio', key='minio_csv_uri')
    csv_filename = context['task_instance'].xcom_pull(task_ids='fetch_outlook_attachment', key='csv_filename')
    
    _ensure_bucket()
    client = _minio_client()
    
    src_key = minio_csv_uri.removeprefix(f"s3://{MINIO_BUCKET}/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst_key = f"{MINIO_BAK_PATH}/{ts}_{csv_filename}"
    
    # Copiar a backup
    client.copy_object(MINIO_BUCKET, dst_key, CopySource(MINIO_BUCKET, src_key))
    print(f"✓ CSV movido a backup: s3://{MINIO_BUCKET}/{dst_key}")
    
    # Eliminar original
    client.remove_object(MINIO_BUCKET, src_key)
    print(f"✓ CSV original eliminado de upload/in")
    
    context['task_instance'].xcom_push(key='backup_uri', value=f"s3://{MINIO_BUCKET}/{dst_key}")
    
    return {'status': 'success', 'backup_uri': f"s3://{MINIO_BUCKET}/{dst_key}"}

# ============================================================================
# TASKS
# ============================================================================

task_fetch = PythonOperator(
    task_id='fetch_outlook_attachment',
    python_callable=fetch_outlook_attachment,
    provide_context=True,
    dag=dag,
)

task_upload_csv = PythonOperator(
    task_id='upload_csv_to_minio',
    python_callable=upload_csv_to_minio,
    provide_context=True,
    dag=dag,
)

task_convert = PythonOperator(
    task_id='csv_to_parquet',
    python_callable=csv_to_parquet,
    provide_context=True,
    dag=dag,
)

task_backup = PythonOperator(
    task_id='move_csv_to_backup',
    python_callable=move_csv_to_backup,
    provide_context=True,
    dag=dag,
)

# ============================================================================
# DEPENDENCIAS
# ============================================================================

task_fetch >> task_upload_csv >> task_convert >> task_backup
