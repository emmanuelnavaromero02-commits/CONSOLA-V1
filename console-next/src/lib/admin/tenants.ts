"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

// Tenant provisioning — backend at /api/admin/tenants (global super_admin/
// owner/admin only). See console/app/main.py api_admin_tenants_create.

export interface Tenant {
  id: string;
  name: string;
  created_at: string | null;
  workspace_count: number;
}

export interface CreateTenantRequest {
  name: string;
  workspace_name?: string;
  admin_email?: string;
  admin_password?: string;
  admin_name?: string;
}

export interface CreateTenantResponse {
  id: string;
  name: string;
  workspace_id: string | null;
  workspace_name: string | null;
  admin_email: string | null;
}

interface TenantsListResponse {
  tenants: Tenant[];
}

export async function listTenants(): Promise<Tenant[]> {
  const { data } = await api.get<TenantsListResponse>("/api/admin/tenants");
  return data.tenants ?? [];
}

export async function createTenant(req: CreateTenantRequest): Promise<CreateTenantResponse> {
  const { data } = await api.post<CreateTenantResponse>("/api/admin/tenants", req);
  return data;
}

export function useTenants() {
  return useQuery<Tenant[]>({
    queryKey: ["admin", "tenants"],
    queryFn: listTenants,
    staleTime: 30_000,
  });
}

export function useCreateTenant() {
  const qc = useQueryClient();
  return useMutation<CreateTenantResponse, Error, CreateTenantRequest>({
    mutationFn: createTenant,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "tenants"] }),
  });
}
