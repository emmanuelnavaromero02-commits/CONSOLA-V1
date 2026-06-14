"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

// Workspace provisioning — backend at /api/admin/workspaces (global
// super_admin/owner/admin only). See console/app/main.py
// api_admin_workspaces_create.

export interface Workspace {
  id: string;
  name: string;
  tenant_id: string;
  tenant_name: string;
  created_at: string | null;
}

export interface CreateWorkspaceRequest {
  tenant_id: string;
  name: string;
}

interface WorkspacesListResponse {
  workspaces: Workspace[];
}

export async function listWorkspaces(tenantId?: string): Promise<Workspace[]> {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : "";
  const { data } = await api.get<WorkspacesListResponse>(`/api/admin/workspaces${qs}`);
  return data.workspaces ?? [];
}

export async function createWorkspace(req: CreateWorkspaceRequest): Promise<Workspace> {
  const { data } = await api.post<Workspace>("/api/admin/workspaces", req);
  return data;
}

export function useWorkspaces(tenantId: string | null) {
  return useQuery<Workspace[]>({
    queryKey: ["admin", "workspaces", tenantId ?? "all"],
    queryFn: () => listWorkspaces(tenantId ?? undefined),
    staleTime: 30_000,
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation<Workspace, Error, CreateWorkspaceRequest>({
    mutationFn: createWorkspace,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
}
