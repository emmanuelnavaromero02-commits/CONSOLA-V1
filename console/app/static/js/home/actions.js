import { hasPermission, state } from './state.js';
import { humanizePermission } from '../i18n/labels.js';

export function permissionText(permission) {
  return humanizePermission(permission);
}

export function action(label, href, permission, hint) {
  const allowed = !permission || hasPermission(permission);
  return {
    label,
    href,
    permission,
    allowed,
    hint: allowed ? (hint || '') : permissionText(permission),
  };
}

export function actionGroups() {
  return {
    crear: [
      action('Nueva conexión Vault', '/viewer/vault', 'vault.connections.write', 'Credenciales de cartuchos.'),
      action('Importar ZIP', '/studio', 'studio.write', 'Se importa desde Studio.'),
      action('Invitar usuario', '/iam', 'iam.users.write', 'Se gestiona desde IAM.'),
    ],
    ejecutar: [
      action('Abrir Workspace', '/workspace', 'workspace.access'),
      action('Abrir Copiloto', '/copilot', 'copilot.use'),
      action('Configurar credenciales', '/viewer/vault', 'vault.connections.write'),
      action('Ver flujos automáticos', '/monitor', 'pipelines.read'),
      action('Ejecutar extracción', '/studio', 'cartridges.execute', 'Ejecuta desde Studio.'),
      action('Ver trabajos recientes', '/viewer/jobs', 'monitor.read'),
    ],
    revisar: [
      action('Trabajos recientes', '/viewer/jobs', 'monitor.read'),
      action('Auditoría', '/security', 'security.audit.read'),
      action('Sesiones', '/iam', 'security.sessions.read'),
      action('Estado del sistema', '/', null, 'Revisa el panel de estado.'),
    ],
    configurar: [
      action('IAM', '/iam', 'iam.users.read'),
      action('Usuarios IAM', '/iam?tab=users', 'iam.users.read'),
      action('Vault', '/viewer/vault', 'vault.connections.read'),
      action('Security Center', '/security', 'security.audit.read'),
    ],
  };
}

export function quickActions() {
  return [
    action('Abrir Workspace', '/workspace', 'workspace.access'),
    action('Abrir Copiloto', '/copilot', 'copilot.use'),
    action('Configurar credenciales', '/viewer/vault', 'vault.connections.write'),
    action('Abrir Studio', '/studio', 'studio.read'),
    action('Ver Operaciones', '/operations', 'operations.read'),
    action('Gestionar IAM', '/iam', 'iam.users.read'),
  ];
}
