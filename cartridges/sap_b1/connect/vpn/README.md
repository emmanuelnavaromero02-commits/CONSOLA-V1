# Túnel WireGuard entre el servidor del cliente y la plataforma OMEGA

Kit de la opción recomendada para que la plataforma lea SAP Business One
(SAP HANA) del cliente: un túnel WireGuard **saliente** desde el servidor
Windows del cliente hacia un host nuestro. Este documento sirve a la TI del
cliente (qué instala, qué alcanza el túnel, qué abre en su cortafuegos) y a
nosotros (qué configurar en AWS y en qué orden).

Todos los valores concretos son marcadores `<...>`; los reales se
intercambian por un canal seguro y nunca se guardan en este repositorio.

## Qué es y qué no es

* WireGuard es software estándar y de código abierto (cliente oficial para
  Windows, firmado por WireGuard LLC; paquete del sistema en Linux). No es
  software nuestro.
* El túnel lo **inicia el servidor del cliente**: una única salida UDP hacia
  `<OMEGA_VPN_PUBLIC_IP>:<WG_LISTEN_PORT>`. **Ningún puerto de entrada** en
  la red del cliente.
* Por el túnel solo circula lo que cada lado permite (`AllowedIPs`):
  * en el servidor del cliente, únicamente nuestra dirección de túnel
    `<WG_SERVER_TUNNEL_IP>`; ningún otro tráfico del servidor cambia de ruta;
  * en nuestro lado, únicamente la dirección de túnel del cliente
    `<CUSTOMER_PEER_TUNNEL_IP>`, y el cortafuegos de nuestro host solo deja
    pasar hacia ella TCP al puerto SQL del tenant `<TENANT_SQL_PORT>`.
* Lo que la plataforma alcanza es **un puerto en un host**: el puerto SQL del
  tenant de HANA, con un usuario de solo lectura limitado a los tres esquemas
  del piloto (`../hana/01_create_readonly_user.sql`).
* Las claves son un par por lado (Curve25519). La clave privada de cada lado
  se genera en su propio equipo y no se envía nunca; solo se intercambian las
  públicas. Cualquiera de los dos lados puede cortar el túnel en cualquier
  momento (desactivar el túnel, retirar el peer, cerrar la regla del
  cortafuegos).

```
  Red del cliente                          Internet                 AWS (OMEGA)
  ┌──────────────────────────┐                              ┌──────────────────────────┐
  │ Servidor Windows         │   UDP saliente               │ Bastión VPN (IP pública) │
  │  WireGuard (cliente)  ───┼──────────────────────────────┼─► WireGuard <WG_IFACE>   │
  │  relevo :<TENANT_SQL_PORT>│  <OMEGA_VPN_PUBLIC_IP>       │   reenvío + NAT          │
  │      │                   │  :<WG_LISTEN_PORT>           │        │ red privada VPC  │
  │      ▼                   │                              │        ▼                 │
  │ Servidor HANA (Linux)    │                              │ Host de la plataforma    │
  │  tenant :<TENANT_SQL_PORT>│                             │   contenedor sap-b1      │
  └──────────────────────────┘                              └──────────────────────────┘
```

## Lo que instala y configura la TI del cliente

1. **WireGuard para Windows** en el servidor Windows (instalador MSI
   oficial). Sin reinicio.
2. **Par de claves en ese servidor**: en la aplicación, "Añadir túnel" →
   "Añadir túnel vacío" genera el par. Copien la **clave pública** (una
   línea en base64) y envíennosla por el canal seguro acordado. La privada
   no sale del servidor.
3. **Configuración del túnel**: peguen el contenido de
   `client/wg-client.conf.template` con los valores que les enviamos
   (nuestra clave pública, `<OMEGA_VPN_PUBLIC_IP>`, `<WG_LISTEN_PORT>`,
   `<CUSTOMER_PEER_TUNNEL_IP>`, `<WG_SERVER_TUNNEL_IP>`), dejando la clave
   privada que generó la aplicación. Activen el túnel. La aplicación lo
   registra como servicio de Windows: sigue activo al cerrar la sesión y
   tras un reinicio.
4. **Relevo del puerto SQL hacia el servidor de HANA** (si HANA corre en
   otro servidor, lo habitual). En PowerShell como administrador, en el
   servidor Windows:

   ```powershell
   # La dirección de túnel de este servidor escucha en el puerto SQL y lo
   # reenvía al servidor de HANA. Solo ese puerto, solo hacia ese host.
   netsh interface portproxy add v4tov4 listenaddress=<CUSTOMER_PEER_TUNNEL_IP> listenport=<TENANT_SQL_PORT> connectaddress=<HANA_LAN_IP> connectport=<TENANT_SQL_PORT>
   Set-Service iphlpsvc -StartupType Automatic; Start-Service iphlpsvc   # el relevo depende de "IP Helper"
   # Regla de cortafuegos de Windows: entrada TCP al puerto SQL solo desde
   # nuestra dirección de túnel.
   New-NetFirewallRule -DisplayName "OMEGA SAP B1 tunnel" -Direction Inbound -Protocol TCP -LocalPort <TENANT_SQL_PORT> -LocalAddress <CUSTOMER_PEER_TUNNEL_IP> -RemoteAddress <WG_SERVER_TUNNEL_IP> -Action Allow
   netsh interface portproxy show v4tov4
   ```

   Variante: si su TI lo prefiere, el cliente WireGuard puede instalarse
   directamente en el servidor Linux de HANA (paquete `wireguard-tools`,
   misma configuración `client/wg-client.conf.template`). Entonces no hay
   relevo y HANA escucha en su puerto habitual; nada cambia en nuestro lado.
5. **Cortafuegos perimetral**: permitir la **salida** UDP desde el servidor
   Windows hacia `<OMEGA_VPN_PUBLIC_IP>:<WG_LISTEN_PORT>`. Nada de entrada.
   Indíquennos la **IP pública** con la que sale ese servidor
   (`<CUSTOMER_PUBLIC_IP>`): nuestro lado solo acepta el túnel desde ella.
6. **Prueba desde su lado**: `../hana/test_connection.ps1` en el servidor
   Windows contra el servidor de HANA confirma usuario, puerto y esquemas
   antes de tocar el túnel. Una vez activo, en la aplicación de WireGuard
   debe verse "Último handshake" reciente (menos de dos minutos) y tráfico
   en ambos sentidos.

## Lo que hacemos nosotros, en orden

| Paso | Qué | Necesita del cliente |
|---|---|---|
| 0 | `server/install_wireguard_host.sh --keys-only` en el bastión: instala WireGuard, genera **nuestro** par de claves en `/etc/wireguard` (modo 600) e imprime solo la pública, que enviamos al cliente. | Nada |
| 1 | `server/open_security_group.sh`: regla de entrada UDP `<WG_LISTEN_PORT>` en el security group del bastión **solo desde `<CUSTOMER_PUBLIC_IP>/32`**; regla TCP `<TENANT_SQL_PORT>` del host de la plataforma hacia el bastión; ruta de la VPC para la subred del túnel. | **Su IP pública** |
| 2 | `server/install_wireguard_host.sh` completo: escribe `<WG_IFACE>.conf` con el peer del cliente, activa el reenvío y las reglas, arranca `wg-quick@<WG_IFACE>`. | **Su clave pública** |
| 3 | En la plataforma: `SAP_B1_HOST=<CUSTOMER_PEER_TUNNEL_IP>`, `SAP_B1_PORT=<TENANT_SQL_PORT>` (`../config/env.sap_b1.template`); `../hana/test_connection.sh` desde el bastión y después `POST /skills/test_connection` del cartucho. | Que su túnel esté activo |

Los pasos 0 y 2 pueden prepararse antes de recibir la IP pública; el paso 1
y la entrada `[Peer]` del paso 2 no.

### Por qué el túnel termina en el bastión y no en el host de la plataforma

En el despliegue actual el host de la plataforma está en una subred privada
sin dirección pública (sale a Internet por un NAT gateway); el único host con
IP pública es el bastión de la VPN del equipo. Por eso:

* el bastión ya tiene `source_dest_check` desactivado y ejecuta WireGuard
  del equipo (wg-easy) dentro de Docker con su propia interfaz; nuestro
  túnel del cliente usa otra interfaz de kernel y otro puerto UDP, sin
  tocar el del equipo;
* el host de la plataforma llega al túnel por la red privada de la VPC:
  `open_security_group.sh` añade la ruta `<WG_SUBNET_CIDR>` → bastión en la
  tabla de rutas privada y permite en el security group del bastión el
  puerto SQL desde el security group de la plataforma;
* en el bastión, `install_wireguard_host.sh` activa `ip_forward` y en
  `PostUp` de la interfaz: `FORWARD` solo para TCP `<TENANT_SQL_PORT>` (e
  ICMP de diagnóstico) hacia `<CUSTOMER_PEER_TUNNEL_IP>` y las respuestas
  establecidas; `MASQUERADE` al salir por la interfaz, para que el cliente
  vea como origen nuestra dirección de túnel, la única que su `AllowedIPs`
  admite. Docker en el host de la plataforma no necesita nada: el
  contenedor sap-b1 sale por la pasarela del host y sigue la ruta de la VPC.

Si algún día la plataforma corre en un host con IP pública propia, el mismo
script vale allí sin cambios: entonces Docker y WireGuard comparten host y
las reglas de `PostUp` cubren el tráfico del puente de Docker igual que hoy
cubren el de la VPC. Las reglas creadas con la CLI de AWS deben pasarse a
Terraform (`infra/terraform/infra/security_groups.tf`, `vpc.tf`) al cerrar
el piloto, para que no queden como deriva.

## Operación

* Estado del túnel en el bastión: `sudo wg show <WG_IFACE>` muestra el peer,
  el último handshake (con keepalive de 25 s debe ser menor de dos minutos) y
  los bytes en cada sentido. Nunca muestra claves privadas.
* Reinicio del túnel: `sudo systemctl restart wg-quick@<WG_IFACE>`.
* Rotación de claves: cualquier lado regenera su par, comunica la nueva
  pública y el otro la sustituye (`[Peer] PublicKey`); un reinicio del túnel
  y listo. Rotar el usuario de HANA es independiente
  (`../hana/03_revoke_readonly_user.sql` y `01`).
* Revocación: el cliente desactiva el túnel o retira el peer; nosotros
  retiramos el `[Peer]` y la regla del security group
  (`open_security_group.sh --revoke`). Cualquiera de las dos cosas basta.
* Si cambia la IP pública del cliente (NAT dinámico), el túnel se reconecta
  solo, pero la regla del security group es fija: hay que actualizarla.
  Pidan a su proveedor una IP fija o un rango.

## Preguntas habituales de la TI del cliente

* **¿Pueden entrar a nuestra red?** Solo a un puerto de un host, y solo
  desde nuestra dirección de túnel: el relevo de Windows escucha únicamente
  en ese puerto y el cortafuegos de Windows lo limita al origen del túnel.
  Nada más es alcanzable, y su lado puede auditarlo (`netsh interface
  portproxy show v4tov4`, registro del cortafuegos) y cortarlo.
* **¿Qué datos salen?** Consultas SQL de solo lectura sobre 45 tablas de los
  tres esquemas acordados, cifradas dos veces (TLS de HANA dentro del túnel
  WireGuard). El detalle está en `cartridges/sap_b1/README.md`.
* **¿Y si la plataforma se cae?** El túnel se queda esperando; no hay
  reintentos agresivos ni efecto sobre HANA. Sin handshake no hay tráfico.
