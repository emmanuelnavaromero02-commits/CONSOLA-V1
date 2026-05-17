export const STATUS_LABELS = {
  online: 'En línea',
  offline: 'Fuera de línea',
  unknown: 'No verificado',
  loading: 'Cargando',
  healthy: 'Correcto',
  protected: 'Protegido',
  configured: 'Configurado',
  limited: 'Acceso limitado',
  blocked: 'Bloqueado',
  unavailable: 'Servicio no disponible',
};

export const ROUTE_LABELS = {
  '/': 'Inicio',
  '/workspace': 'Área de trabajo',
  '/monitor': 'Monitor',
  '/studio': 'Studio',
  '/iam': 'IAM / Accesos',
  '/security': 'Seguridad',
  '/admin/users': 'IAM / Usuarios',
  '/viewer/vault': 'Caja fuerte',
  '/viewer/jobs': 'Trabajos recientes',
  '/viewer/datasets': 'Reportes de datos',
  '/decisions': 'Decisiones',
};

export const PERMISSION_LABELS = {
  'workspace.access': 'No tienes acceso al área de trabajo. Pide acceso a Workspace.',
  'monitor.read': 'No tienes permiso para ver el monitor.',
  'studio.read': 'No tienes permiso para abrir Studio.',
  'studio.write': 'No tienes permiso para configurar fuentes de datos en Studio.',
  'iam.users.read': 'No tienes permiso para ver usuarios. Pide acceso de lectura IAM.',
  'iam.users.write': 'No tienes permiso para invitar o editar usuarios. Pide acceso de escritura IAM.',
  'iam.roles.read': 'No tienes permiso para ver roles y permisos.',
  'iam.policies.read': 'No tienes permiso para ver reglas de acceso.',
  'security.audit.read': 'No tienes permiso para ver auditoría. Pide acceso de seguridad.',
  'security.sessions.read': 'No tienes permiso para ver sesiones activas.',
  'security.sessions.revoke': 'No tienes permiso para revocar sesiones.',
  'vault.connections.read': 'No tienes permiso para ver conexiones de la caja fuerte.',
  'vault.connections.write': 'No tienes permiso para configurar conexiones de la caja fuerte.',
  'vault.secrets.read_masked': 'No tienes permiso para ver secretos ocultos.',
  'vault.secrets.reveal': 'No tienes permiso para revelar secretos protegidos.',
  'pipelines.read': 'No tienes permiso para ver flujos automáticos.',
  'pipelines.run': 'No tienes permiso para ejecutar flujos automáticos.',
  'pipelines.write': 'No tienes permiso para modificar flujos automáticos.',
  'datasets.read': 'No tienes permiso para ver reportes de datos.',
  'datasets.write': 'No tienes permiso para modificar reportes de datos.',
  'datasets.delete': 'No tienes permiso para eliminar reportes de datos.',
};

export const ERROR_LABELS = {
  unauthorized: 'Tu sesión no está autorizada para esta acción.',
  forbidden: 'No tienes permiso para realizar esta acción.',
  not_found: 'No encontramos la información solicitada.',
  server_error: 'El servicio no respondió correctamente. Intenta de nuevo o revisa logs.',
  network: 'No pudimos conectar con el servicio.',
  unknown: 'Ocurrió un error inesperado.',
};
