# Runbook AWS live SAP SuccessFactors

Comando unico:

```bash
make sap-successfactors-aws-live-max
```

Variables opcionales:

```bash
OMEGA_AWS_INSTANCE_ID=<aws-instance-id>
AWS_REGION=us-east-1
PUBLIC_CONSOLE_URL=http://modecissions-public-255609366.us-east-1.elb.amazonaws.com
OMEGA_TENANT_ID=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78
OMEGA_WORKSPACE_ID=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4
OMEGA_SF_CONN_ID=femsa_sf
OMEGA_SF_LIVE_MAX_WAIT_SECONDS=2400
```

El runner:

- valida identidad AWS real;
- entra a la instancia por SSM;
- valida contenedores reales y DAGs Airflow;
- dispara `sap_successfactors_extract_all` salvo que `OMEGA_SF_LIVE_TRIGGER_EXTRACT=0`;
- valida registros de extraccion en Postgres/RDS;
- lista prefijos S3 reales de Bronze, Silver y Gold;
- materializa la fundacion Gold segura existente;
- valida Control Room, catalogo, capa semantica, apps, KBs y agents desde el stack real;
- deja Copilot como `BLOCKED/NOT_EXECUTED` si no hay sesion bearer live, sin sustituir con mock.

El comando no imprime secretos ni valores de PII; los reportes contienen conteos, estados y errores redactados.
