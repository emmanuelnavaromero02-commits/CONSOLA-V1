# Acceso a MODecissions

MODecissions corre en AWS detrás de una VPN WireGuard. **No hay servicios expuestos a internet**: para acceder a cualquier UI necesitas tener la VPN activa.

## Requisitos

- Instalar **WireGuard** para tu sistema operativo: <https://www.wireguard.com/install/>
- Solicitar tu archivo `.conf` al admin (ver siguiente sección).

## Obtener tu acceso

1. El admin entra a `http://{VPN_IP}:51821` (panel wg-easy).
2. Crea un peer con tu nombre (ej: `laptop-juan`).
3. Te comparte el archivo `.conf` por canal seguro (1Password, Bitwarden, etc.).

> El `.conf` contiene tu llave privada. **No lo compartas ni lo subas a Git.**

## Conectarte

1. Abrir la app de WireGuard.
2. Importar el tunnel desde el `.conf` que te pasaron.
3. Activar el tunnel.
4. Acceder a los servicios:

| Servicio   | URL                    |
|------------|------------------------|
| Console    | http://{APP_IP}:8000   |
| Superset   | http://{APP_IP}:8088   |
| Airflow    | http://{APP_IP}:8082   |
| Refinement | http://{APP_IP}:8500   |
| RAG        | http://{APP_IP}:8600   |
| MCP-Infra  | http://{APP_IP}:8010   |

(Reemplaza `{APP_IP}` por la IP privada de la EC2 App que te pasó el admin.)

## Verificar tu conexión

Desde una PowerShell, prueba:

```powershell
Test-Connection {APP_IP} -Count 2
```

Debe responder. Si no, revisa que el tunnel esté **activo** en la app de WireGuard.

## Regla de oro

**Sin VPN activa = sin acceso.** Los servicios no están expuestos a internet por diseño. Si una URL no carga, el primer paso siempre es verificar que el tunnel esté conectado.

## Troubleshooting rápido

| Síntoma                           | Causa probable                          | Solución                                                  |
|-----------------------------------|-----------------------------------------|-----------------------------------------------------------|
| URL no carga                      | VPN desactivada                         | Activar tunnel en WireGuard                               |
| Tunnel activo pero sin ping       | Handshake no completado                 | Esperar 30s o reiniciar el tunnel                         |
| `latest handshake` > 5 min        | Sesión perdida                          | Toggle off/on del tunnel                                  |
| Servicio responde 502/503         | Container caído en EC2                  | Avisar al admin                                           |
| Velocidad muy lenta               | Salir de la VPN para tráfico no-MODe    | Configurar split-tunnel (consultar admin)                 |
