FROM apache/airflow:2.9.2

USER airflow

RUN pip install --no-cache-dir \
    requests \
    pandas \
    pyarrow \
    minio \
    psycopg2-binary \
    openpyxl
