# IAM — Flujo unificado de gestión de usuarios

Sprint v1.1 — documentación del flujo entre las dos pantallas que tocan
usuarios.

## Dos pantallas, una intención

| Pantalla | URL | Responsabilidad |
| --- | --- | --- |
| Admin de usuarios | `/admin/users` | CRUD básico: alta, baja, edición de nombre, **rol base** (`user` / `admin`), activar/desactivar, resetear password. |
| IAM / Access Center | `/iam` | Permisos finos (overrides), roles personalizados, sesiones, auditoría, reglas de acceso, caja fuerte. |

Ambas pantallas requieren el permiso `iam.users.read` (lectura) y
`iam.users.write` (escritura), por lo que solo administradores las ven.

## Flujo recomendado

1. **Alta inicial**: crear el usuario desde `/admin/users` con rol base
   (`user` por defecto). El backend asigna automáticamente workspace y rol
   por defecto al registrar al usuario (ver PR #91).
2. **Edición de rol/estado**: cambia el rol base o activa/desactiva al
   usuario desde el modal **Editar usuario** en `/admin/users`.
3. **Permisos finos**: si necesitas excepciones (otorgar/revocar un permiso
   puntual a un usuario sin tocar su rol), abre el modal de edición y haz
   clic en **PERMISOS FINOS →**. Esto te lleva a
   `/iam?user_id=<id>` con la pestaña *Usuarios* preseleccionada.
4. **Volver**: desde cualquier sección de `/iam`, el link **← Volver a
   admin de usuarios** en la barra lateral regresa a `/admin/users`.

## Sincronización entre pantallas

- El rol base vive en la columna `role` de la tabla `users`. Ambas
  pantallas lo leen de la misma fuente, por lo que un cambio en
  `/admin/users` se refleja al recargar `/iam`.
- Los overrides de permisos viven en tablas IAM dedicadas y solo se
  editan desde `/iam`.
- Si encuentras divergencia visual entre las dos pantallas, refresca
  ambas (el caché de IAM se invalida con el botón **Actualizar**).

## Crosslinks implementados (v1.1)

- `/admin/users` → botón **PERMISOS FINOS →** en el modal de edición,
  con `user_id` propagado por query string.
- `/iam` → link **← Volver a admin de usuarios** en la barra lateral.
- `/iam?user_id=<id>` → la pestaña *Usuarios* se selecciona
  automáticamente al cargar.

## Pendiente (v1.2)

- Renderizar un highlight visual de la fila del usuario preseleccionado
  cuando se llega vía `?user_id=...`.
- Modal de edición de permisos finos dentro de la propia tabla de
  usuarios de `/iam` (en lugar de las pestañas separadas).
