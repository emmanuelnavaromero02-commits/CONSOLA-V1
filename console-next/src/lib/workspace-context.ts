// Name of the cookie that holds the user's selected workspace. The shell
// switcher writes it; the API client (lib/api.ts) reads it and echoes the
// value as the `X-Workspace-Id` header so every request is scoped to the
// chosen workspace. Backend resolves/authorizes it in the auth middleware.
export const ACTIVE_WORKSPACE_COOKIE = "omega_active_workspace";
