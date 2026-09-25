<#
.SYNOPSIS
  Prueba de conectividad a SAP HANA (SAP Business One) desde el servidor
  Windows del cliente.

.DESCRIPTION
  Paso 1  Comprueba que el puerto SQL del tenant responde (Test-NetConnection).
  Paso 2  Si el cliente de SAP HANA (hdbsql) está instalado, abre una sesión con
          el usuario de solo lectura y lee "Version" de la tabla CINF de cada
          empresa: la misma consulta que usa la plataforma.

  Ninguna credencial viaja por la línea de comandos: hdbsql pide la contraseña
  en pantalla, o se usa una clave del hdbuserstore (-UserStoreKey). No use -p.

.PARAMETER HanaHost
  Host de HANA. Por defecto, la variable de entorno SAP_B1_HOST.
.PARAMETER Port
  Puerto SQL del tenant (ver 00_find_tenant_sql_port.sql). Por defecto
  SAP_B1_PORT o 30015.
.PARAMETER User
  Usuario técnico de solo lectura. Por defecto SAP_B1_USER.
.PARAMETER TenantDb
  Nombre del tenant; solo hace falta si Port es el puerto de SYSTEMDB (3NN13).
  Por defecto SAP_B1_DATABASE.
.PARAMETER CompanyDb
  Esquemas de empresa a probar. Admite también la forma alias=ESQUEMA,... de
  SAP_B1_COMPANIES (por defecto se lee esa variable).
.PARAMETER UserStoreKey
  Clave del hdbuserstore creada con:  hdbuserstore SET <CLAVE> <HANA_HOST>:<PUERTO> <USUARIO>
  Si se indica, no se pide contraseña.
.PARAMETER Encrypt
  TLS entre este servidor y HANA (por defecto activado).
.PARAMETER SkipCertValidation
  Confía en el certificado del servidor sin validarlo (instalaciones con
  certificado autofirmado). Solo para la prueba.

.EXAMPLE
  .\test_connection.ps1 -HanaHost <HANA_HOST> -Port <TENANT_SQL_PORT> -User <OMEGA_B1_READER> -CompanyDb <COMPANY_DB_1>,<COMPANY_DB_2>,<COMPANY_DB_3>
#>
[CmdletBinding()]
param(
    [string]$HanaHost = $env:SAP_B1_HOST,
    [int]$Port = $(if ($env:SAP_B1_PORT) { [int]$env:SAP_B1_PORT } else { 30015 }),
    [string]$User = $env:SAP_B1_USER,
    [string]$TenantDb = $env:SAP_B1_DATABASE,
    [string[]]$CompanyDb = @(),
    [string]$UserStoreKey = "",
    [bool]$Encrypt = $true,
    [switch]$SkipCertValidation
)

$ErrorActionPreference = "Stop"

function Read-Required([string]$Value, [string]$Prompt) {
    # Ask on screen for anything not given as a parameter or env var.
    if ([string]::IsNullOrWhiteSpace($Value)) { return (Read-Host -Prompt $Prompt) }
    return $Value
}

function Get-CompanySchemas([string[]]$Given) {
    # Accept plain schema names or the alias=SCHEMA pairs used by SAP_B1_COMPANIES.
    $raw = @()
    if ($Given.Count -gt 0) { $raw = $Given } elseif ($env:SAP_B1_COMPANIES) { $raw = $env:SAP_B1_COMPANIES -split "[,;]" }
    $schemas = @()
    foreach ($item in $raw) {
        $part = $item.Trim()
        if (-not $part) { continue }
        if ($part.Contains("=")) { $part = $part.Split("=", 2)[1].Trim() }
        # Schema names are identifiers: letters, digits, _ $ and -. Anything else is refused.
        if ($part -notmatch '^[A-Za-z0-9_$][A-Za-z0-9_$\-]{0,127}$') { throw "Nombre de esquema no válido: $part" }
        $schemas += $part
    }
    return $schemas
}

function Find-Hdbsql {
    $cmd = Get-Command hdbsql.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles "SAP\hdbclient\hdbsql.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "SAP\hdbclient\hdbsql.exe")
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

$HanaHost = Read-Required $HanaHost "Host de HANA"
$User     = Read-Required $User "Usuario de solo lectura"
$schemas  = Get-CompanySchemas $CompanyDb
if ($schemas.Count -eq 0) {
    $schemas = Get-CompanySchemas @((Read-Host -Prompt "Esquemas de empresa (separados por coma)"))
}

Write-Host ""
Write-Host "== Paso 1: puerto TCP $HanaHost`:$Port =="
$tnc = Test-NetConnection -ComputerName $HanaHost -Port $Port -WarningAction SilentlyContinue
if (-not $tnc.TcpTestSucceeded) {
    Write-Host "FALLO: el puerto no responde." -ForegroundColor Red
    Write-Host "  - Compruebe el cortafuegos entre este servidor y HANA."
    Write-Host "  - Compruebe el puerto: 3NN13 es SYSTEMDB, el tenant suele responder en 3NN15 (00_find_tenant_sql_port.sql)."
    exit 2
}
Write-Host "OK: el puerto responde." -ForegroundColor Green

Write-Host ""
Write-Host "== Paso 2: sesión SQL con hdbsql =="
$hdbsql = Find-Hdbsql
if (-not $hdbsql) {
    Write-Host "hdbsql no está instalado en este servidor; el paso 2 se omite." -ForegroundColor Yellow
    Write-Host "  El cliente de SAP HANA (paquete 'SAP HANA Client') se descarga del portal de software de SAP."
    Write-Host "  El paso 1 ya confirma que la red llega al puerto SQL."
    exit 0
}

# hdbsql runs one input file so the password is asked once for every company.
$sqlFile = [System.IO.Path]::GetTempFileName()
try {
    $lines = @()
    foreach ($schema in $schemas) { $lines += ('SELECT ''' + $schema + ''' AS COMPANY_DB, "Version" FROM "' + $schema + '"."CINF";') }
    Set-Content -Path $sqlFile -Value $lines -Encoding ASCII

    $args = @("-n", "$HanaHost`:$Port")
    if ($TenantDb) { $args += @("-d", $TenantDb) }
    if ($UserStoreKey) { $args += @("-U", $UserStoreKey) } else { $args += @("-u", $User) }   # no -p: hdbsql asks on screen
    if ($Encrypt) { $args += "-e" }
    if ($SkipCertValidation) { $args += "-ssltrustcert" }
    $args += @("-A", "-I", $sqlFile)

    Write-Host "Ejecutando: hdbsql $($args -join ' ')"
    & $hdbsql @args
    $code = $LASTEXITCODE
} finally {
    Remove-Item -Path $sqlFile -Force -ErrorAction SilentlyContinue
}

if ($code -ne 0) {
    Write-Host "FALLO: hdbsql devolvió el código $code." -ForegroundColor Red
    Write-Host "  - 'authentication failed': usuario o contraseña; revise 01_create_readonly_user.sql."
    Write-Host "  - 'insufficient privilege' o 'invalid schema name': falta el GRANT SELECT sobre ese esquema."
    Write-Host "  - Error de TLS: pruebe con -SkipCertValidation para confirmar que es el certificado."
    exit 3
}
Write-Host "OK: cada empresa devolvió su versión de Business One." -ForegroundColor Green
exit 0
