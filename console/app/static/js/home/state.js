export const state = {
  user: null,
  role: 'anonymous',
  workspaceUrl: '/workspace',
  permissions: null,
  effectivePermissions: new Set(),
  usersSummary: null,
  statuses: {},
  activity: {},
  loading: true,
  errors: [],
  meAccess: null,
};

export function setState(patch) {
  Object.assign(state, patch);
}

const fallbackPermissions = {
  owner: [
    'workspace.access', 'monitor.read', 'studio.read', 'studio.write',
    'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write',
    'iam.users.read', 'iam.users.write', 'iam.roles.read',
    'security.audit.read', 'security.sessions.read',
    'vault.connections.read', 'vault.connections.write',
    'operations.read', 'settings.read', 'settings.write',
    'cartridges.read', 'cartridges.write', 'cartridges.execute',
    'marketplace.read', 'marketplace.request', 'marketplace.write', 'marketplace.admin',
    'copilot.use', 'copilot.write', 'copilot.execute',
  ],
  super_admin: [
    'workspace.access', 'monitor.read', 'studio.read', 'studio.write',
    'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write',
    'iam.users.read', 'iam.users.write', 'iam.roles.read',
    'security.audit.read', 'security.sessions.read',
    'vault.connections.read', 'vault.connections.write',
    'operations.read', 'settings.read', 'settings.write',
    'cartridges.read', 'cartridges.write', 'cartridges.execute',
    'marketplace.read', 'marketplace.request', 'marketplace.write', 'marketplace.admin',
    'copilot.use', 'copilot.write', 'copilot.execute',
  ],
  admin: [
    'workspace.access', 'monitor.read', 'studio.read', 'studio.write',
    'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write',
    'iam.users.read', 'iam.users.write', 'iam.roles.read',
    'security.audit.read', 'security.sessions.read',
    'vault.connections.read', 'vault.connections.write',
    'operations.read', 'settings.read', 'settings.write',
    'cartridges.read', 'cartridges.write', 'cartridges.execute',
    'marketplace.read', 'marketplace.request', 'marketplace.write', 'marketplace.admin',
    'copilot.use', 'copilot.write', 'copilot.execute',
  ],
  security_admin: ['monitor.read', 'iam.users.read', 'iam.users.write', 'iam.roles.read', 'security.audit.read', 'security.sessions.read', 'vault.connections.read', 'copilot.use'],
  workspace_admin: ['workspace.access', 'monitor.read', 'studio.read', 'studio.write', 'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write', 'vault.connections.read', 'vault.connections.write', 'cartridges.read', 'cartridges.write', 'cartridges.execute', 'marketplace.read', 'marketplace.request', 'copilot.use', 'copilot.write', 'copilot.execute'],
  analyst: ['workspace.access', 'monitor.read', 'studio.read', 'pipelines.read', 'datasets.read', 'cartridges.read', 'marketplace.read', 'marketplace.request', 'copilot.use'],
  auditor: ['monitor.read', 'security.audit.read', 'security.sessions.read', 'iam.roles.read'],
  viewer: ['workspace.access', 'monitor.read', 'studio.read', 'pipelines.read', 'datasets.read', 'cartridges.read', 'marketplace.read', 'copilot.use'],
  workspace_user: ['workspace.access', 'marketplace.read', 'marketplace.request'],
  user: ['workspace.access', 'monitor.read', 'studio.read', 'cartridges.read', 'marketplace.read', 'copilot.use'],
};

export function applyPermissionsFromRole(role) {
  const keys = fallbackPermissions[role] || [];
  state.effectivePermissions = new Set(keys);
}

export function hasPermission(permission) {
  return state.effectivePermissions.has(permission);
}
