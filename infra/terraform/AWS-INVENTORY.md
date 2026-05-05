# Inventario AWS — MODecissionsPaaS

Cuenta `980921755079`, región `us-east-1`. Todo gestionado vía Terraform desde
[`infra/terraform/infra/`](infra/). Para ver el estado real:
`cd infra/terraform/infra && terraform output`.

> **Última actualización**: 2026-05-02 (deploy A1 fresh).
> Las IDs concretas (instance IDs, IPs, bucket suffix) cambian en cada
> `terraform destroy` + `apply`. Los identificadores listados aquí son los
> del deploy actual; la **estructura** es estable.

---

## 1. Cómputo (2 EC2)

| Recurso | Tipo / ID | IP | Función |
|---|---|---|---|
| **EC2 VPN** (bastion) | `t3.nano` · `i-0ffd81fa030577b11` | EIP pública `34.239.194.46` (subred pública `10.0.1.0/24`) | Corre WireGuard (wg-easy en Docker, puertos 51820/UDP + 51821/TCP). Es la **única puerta de entrada al sistema** — el equipo se conecta vía VPN para alcanzar la App EC2 |
| **EC2 App** | `m6i.xlarge` (4 vCPU, 16GB RAM) · `i-00b8bd8b069146b8d` | privada `10.0.2.175` (subred privada `10.0.2.0/24`) | Hospeda **todo el stack en Docker**: console, workspace, refinement, mcp-infra, postgres + postgres_gold, superset, airflow, mailhog. Disco gp3 150 GB encriptado |

**Costo aprox combinado**: ~$140/mes (m6i.xlarge) + $4/mes (t3.nano) = **~$144/mes**

---

## 2. Red (VPC + subnets + gateways)

| Recurso | ID/CIDR | Función |
|---|---|---|
| **VPC** | `vpc-04b3b8e384b4d2ef8` · `10.0.0.0/16` | Aislamiento de toda la infraestructura |
| **Subnet pública** | `10.0.1.0/24` (us-east-1a) | Hospeda la EC2 VPN. Tiene ruta directa al IGW |
| **Subnet privada** | `10.0.2.0/24` (us-east-1a) | Hospeda la EC2 App. Sin acceso entrante directo desde Internet |
| **Subnet privada secundaria** | `10.0.3.0/24` (us-east-1b) | Vacía. Existe solo por requisito de RDS subnet group de cuando había RDS. **Candidata a eliminar** (tag dice `modecissions-private-1b-rds-only`) |
| **Internet Gateway** | `modecissions-igw` | Salida a Internet desde la subnet pública |
| **NAT Gateway** | en subnet pública con EIP propia | Permite a la EC2 App (privada) salir a Internet (S3, GitHub clone, pip, npm, Anthropic API). **Es el ítem más caro relativamente — ~$33/mes solo por estar prendido** |
| **Route table pública** | apunta a IGW | Asociada a subnet pública |
| **Route table privada** | apunta a NAT GW | Asociada a las 2 subnets privadas |

---

## 3. Elastic IPs (2)

| Recurso | IP | Asociado a | Función |
|---|---|---|---|
| **EIP VPN** | `34.239.194.46` | EC2 VPN | URL pública del wg-easy + endpoint UDP de WireGuard. Sobrevive a recreaciones de la EC2 |
| **EIP NAT** | (variable) | NAT Gateway | Outbound IP de la EC2 App. Persiste para que servicios externos (Replicon, Anthropic, etc.) la vean estable si necesitan whitelisting |

---

## 4. Storage

| Recurso | Identificador | Función |
|---|---|---|
| **S3 bucket lakehouse** | `modecissions-lakehouse-0baf85` | Lakehouse (Parquet bronze + silver). Reemplaza MinIO local. Versioning ON, public access bloqueado, transición a Standard-IA tras 30 días |
| **EBS App** | gp3 150 GB encriptado | Root del EC2 App. **Aquí viven los volúmenes Docker de postgres + postgres_gold** — datos de la app residen aquí, no en RDS. Si destruyes la EC2 sin backup pierdes todos los datos |
| **EBS VPN** | gp3 default (8 GB) | Root del EC2 VPN. Solo SO + binarios + configs WireGuard. Datos no críticos — se regeneran en re-deploy |

---

## 5. Seguridad

| Recurso | Permite | Función |
|---|---|---|
| **SG `modecissions-sg-vpn`** (EC2 VPN) | Ingress: UDP 51820, TCP 22, TCP 51821 desde `0.0.0.0/0` · Egress: ALL | Acceso público controlado al WireGuard server |
| **SG `modecissions-sg-app`** (EC2 App) | Ingress: **TODO** desde `sg-vpn` (no IPs públicas) · Egress: ALL | Solo accesible desde el bastión VPN. Cubre 8000, 8001, 8010, 8025, 8082, 8088, 8500, SSH |
| **IAM role `modecissions-app-role`** | SSM Managed Instance Core, S3 full sobre el bucket lakehouse, Bedrock InvokeModel | Permite a la EC2 App leer/escribir S3 sin AWS keys, ser administrada por SSM, llamar a Bedrock |
| **IAM role `modecissions-vpn-role`** | SSM Managed Instance Core | Solo SSM (debugging remoto sin SSH) |
| **Instance profiles** | `modecissions-app-profile`, `modecissions-vpn-profile` | Vinculan los IAM roles a sus respectivas EC2 |
| **Key pair** | `modecissions-key` | SSH key compartida para ambas EC2. Public key en AWS, private key (`.pem`) en `infra/terraform/` (gitignored) |

---

## 6. AMI (data source, no recurso gestionado)

`ami-0xxx` (Ubuntu 22.04 LTS amd64, dinámico). Lo resuelve Terraform en cada apply al `most_recent`. Owner: Canonical (`099720109477`).

---

## Resumen de costos (estimado, on-demand, us-east-1)

| Concepto | Costo/mes |
|---|---|
| EC2 App (m6i.xlarge) | ~$140 |
| EC2 VPN (t3.nano) | ~$4 |
| **NAT Gateway** | **~$33** (730h + bajos GB egress) |
| 2 EIPs | gratis cuando están asociadas |
| S3 lakehouse | ~$2 (50 GB + requests) |
| EBS App (150 GB gp3) | ~$13 |
| EBS VPN (8 GB gp3) | ~$1 |
| Data transfer out | ~$1-3 |
| **Total** | **~$195/mes** |

---

## Optimizaciones futuras (Phase 2)

1. **Borrar la subnet `private-1b-rds-only`** — es deuda de cuando había RDS. Sin uso hoy.
2. **Reemplazar NAT GW por VPC Endpoints**: gateway endpoint para S3 (gratis) cubre el 80% del egress. Endpoint interface para ECR (~$7/mes c/u) si se usa. Ahorra ~$25/mes.
3. **Schedule de stop/start del EC2 App** fuera de horario de oficina (50% del tiempo) → ahorra ~$70/mes.
4. **Backup automatizado** de los volúmenes postgres a S3 (cron + `pg_dumpall` → `aws s3 cp`). Hoy NO hay backups — si la EC2 muere, pierdes todos los datos.

---

## Diagrama de arquitectura

```
  Internet ── EIP VPN (34.239.194.46) ── EC2 VPN (t3.nano, public subnet)
                                              │
                                              │ WireGuard tunnel (UDP 51820)
                                              │
  Laptop ──VPN──► 10.8.0.x ─► VPC route ─► EC2 App (10.0.2.175, m6i.xlarge)
                                                       │
                                                       ├─► postgres / postgres_gold (containers, EBS local)
                                                       ├─► console (8000), workspace (8001), refinement (8500),
                                                       │   mcp-infra (8010), superset (8088), airflow (8082),
                                                       │   mailhog (8025) — todo en docker-compose
                                                       │
                                                       └─► NAT Gateway ──► Internet
                                                                  ▲
                                                                  │
                                                                  └── S3 lakehouse (modecissions-lakehouse-0baf85)
```

---

## Comandos útiles para auditar

```bash
# Lista todas las instancias EC2 del proyecto
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=modecissions" \
  --query "Reservations[].Instances[].[InstanceId,InstanceType,State.Name,PrivateIpAddress,PublicIpAddress]" \
  --output table

# Ver el bucket S3 y su tamaño
aws s3 ls s3://modecissions-lakehouse-0baf85/ --recursive --human-readable --summarize | tail -2

# Verificar EIPs asociadas
aws ec2 describe-addresses \
  --query "Addresses[].{IP:PublicIp, Instance:InstanceId, AssocId:AssociationId}" \
  --output table

# Ver costo del último mes (requiere Cost Explorer habilitado)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["modecissions"]}}'
```

---

## Pausar / reanudar para ahorrar

Costos por hora (us-east-1 on-demand):

| Recurso | $/h corriendo | $/h detenido |
|---|---|---|
| EC2 App (m6i.xlarge) | $0.192 | $0 (EBS sigue $0.018/h) |
| EC2 VPN (t3.nano)    | $0.0052 | $0 |
| NAT Gateway          | $0.045 + $0.045/GB | $0 (eliminado) |

### Pausa corta (≤24h)

```powershell
# Apaga ambas EC2 (~$5/día ahorrado)
aws ec2 stop-instances --instance-ids i-00b8bd8b069146b8d i-0ffd81fa030577b11

# Reanudar
aws ec2 start-instances --instance-ids i-00b8bd8b069146b8d i-0ffd81fa030577b11
# Espera ~2 min — los containers Docker arrancan solos por restart: unless-stopped
```

> Los IDs cambian si destruyes y re-aplicas terraform. Saca los actuales con:
> `terraform output ec2_app_instance_id` / `terraform output ec2_vpn_instance_id`

### Pausa larga (>2 días)

Adicional al stop, elimina el NAT GW (ahorra ~$1/día más):

```powershell
cd C:\MODecissionsPaaS\infra\terraform\infra
terraform destroy -target=aws_nat_gateway.main -target=aws_eip.nat
# Reanudar: terraform apply lo recrea
```

### Costos al momento

```powershell
# Mes en curso por servicio (PowerShell)
aws ce get-cost-and-usage `
  --time-period Start=$(Get-Date -Format "yyyy-MM-01"),End=$(Get-Date -Format "yyyy-MM-dd") `
  --granularity MONTHLY --metrics UnblendedCost `
  --group-by Type=DIMENSION,Key=SERVICE --output table
```

Atajo más rápido sin AWS CLI: AWS Console → Billing → Bills.
