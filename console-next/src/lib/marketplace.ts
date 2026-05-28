import { api } from "@/lib/api";

export type MarketplaceStatus =
  | "available"
  | "active"
  | "pending_approval"
  | "pending_connection"
  | "waiting_credentials"
  | "requested"
  | "ready"
  | "failed"
  | "paused"
  | "revoked"
  | "expired"
  | "suspended"
  | string;

export type AdminInstallationAction = "approve" | "pause" | "revoke" | "reactivate";
export type InstallationAccessMode = "inherit" | "deny";

export interface CommercialProfile {
  headline?: string;
  what_it_does?: string[];
  data_domains?: string[];
  dashboards?: string[];
  sample_questions?: string[];
  requirements?: string[];
  plan?: string;
  price_label?: string;
}

export interface MarketplaceProduct {
  cartridge_id: string;
  name?: string | null;
  description?: string | null;
  category?: string | null;
  access_status?: MarketplaceStatus | null;
  installed?: boolean | null;
  ready?: boolean | null;
  can_request?: boolean | null;
  can_retry?: boolean | null;
  requires_credentials?: boolean | null;
  entity_count?: number | null;
  dataset_count?: number | null;
  app_count?: number | null;
  commercial?: CommercialProfile | null;
}

export interface CartridgeInstallation {
  id: string;
  tenant_id?: string | null;
  tenant_name?: string | null;
  workspace_id?: string | null;
  workspace_name?: string | null;
  cartridge_id: string;
  product_name?: string | null;
  status?: MarketplaceStatus | null;
  access_status?: MarketplaceStatus | null;
  current_step?: string | null;
  error_message?: string | null;
  created_by_email?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  ready_at?: string | null;
  version?: string | null;
  usable?: boolean | null;
  can_retry?: boolean | null;
}

export interface MarketplaceProductsResponse {
  tenant_id?: string | null;
  workspace_id?: string | null;
  products: MarketplaceProduct[];
}

export interface MarketplaceInstallationsResponse {
  tenant_id?: string | null;
  workspace_id?: string | null;
  installations: CartridgeInstallation[];
}

export interface InstallationAccessUser {
  id: number;
  email: string;
  name?: string | null;
  global_role?: string | null;
  workspace_role?: string | null;
  is_active?: boolean | null;
  mode?: InstallationAccessMode | string | null;
  effective_access?: boolean | null;
  base_access?: boolean | null;
  reason?: string | null;
  updated_at?: string | null;
  updated_by_email?: string | null;
}

export interface InstallationAccessResponse {
  installation?: CartridgeInstallation | null;
  users: InstallationAccessUser[];
}

export interface MarketplaceMutationResponse {
  installation?: CartridgeInstallation;
  product?: MarketplaceProduct;
  order_id?: string;
}

export async function listMarketplaceProducts(): Promise<MarketplaceProductsResponse> {
  const { data } = await api.get<MarketplaceProductsResponse>("/api/marketplace/products");
  return { ...data, products: data.products ?? [] };
}

export async function listCustomerCartridges(): Promise<MarketplaceInstallationsResponse> {
  const { data } = await api.get<MarketplaceInstallationsResponse>("/api/customer/cartridges");
  return { ...data, installations: data.installations ?? [] };
}

export async function requestMarketplaceProduct(cartridgeId: string): Promise<MarketplaceMutationResponse> {
  const { data } = await api.post<MarketplaceMutationResponse>(
    `/api/marketplace/products/${encodeURIComponent(cartridgeId)}/request`,
    {},
  );
  return data;
}

export async function retryMarketplaceInstallation(installationId: string): Promise<MarketplaceMutationResponse> {
  const { data } = await api.post<MarketplaceMutationResponse>(
    `/api/marketplace/installations/${encodeURIComponent(installationId)}/retry`,
    {},
  );
  return data;
}

export async function listAdminInstallations(): Promise<MarketplaceInstallationsResponse> {
  const { data } = await api.get<MarketplaceInstallationsResponse>("/api/admin/installations");
  return { ...data, installations: data.installations ?? [] };
}

export async function runAdminInstallationAction(
  installationId: string,
  action: AdminInstallationAction,
): Promise<MarketplaceMutationResponse> {
  const { data } = await api.post<MarketplaceMutationResponse>(
    `/api/admin/installations/${encodeURIComponent(installationId)}/${action}`,
    {},
  );
  return data;
}

export async function getInstallationAccess(installationId: string): Promise<InstallationAccessResponse> {
  const { data } = await api.get<InstallationAccessResponse>(
    `/api/admin/installations/${encodeURIComponent(installationId)}/access`,
  );
  return { ...data, users: data.users ?? [] };
}

export async function setInstallationUserAccess(
  installationId: string,
  userId: number,
  mode: InstallationAccessMode,
): Promise<InstallationAccessResponse> {
  const { data } = await api.patch<InstallationAccessResponse>(
    `/api/admin/installations/${encodeURIComponent(installationId)}/access/${encodeURIComponent(userId)}`,
    { mode },
  );
  return { ...data, users: data.users ?? [] };
}
