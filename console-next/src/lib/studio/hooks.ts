"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createCartridge,
  createEntity,
  deleteDag,
  deleteDataset,
  deployDag,
  getCartridgeStatus,
  getDagGraph,
  getDagSource,
  getLayerPreview,
  getRuntimeConfig,
  getStudioCartridge,
  getSystemInfo,
  importCartridge,
  introspectSource,
  listDags,
  listStudioCartridges,
  listStudioEntities,
  listTemplates,
  publishSupersetDataset,
  refreshDataset,
  renameDag,
  renameEntity,
  saveDataset,
  updateEntity,
  uploadEntitySpec,
} from "./client";
import type {
  CreateCartridgeInput,
  CreateEntityInput,
  DeployDagInput,
  EntityPatch,
  SaveDatasetInput,
  StudioLayer,
} from "./types";

const ROOT = "studio";

export const studioKeys = {
  cartridges: () => [ROOT, "cartridges"] as const,
  cartridge: (id: string) => [ROOT, "cartridge", id] as const,
  status: (id: string) => [ROOT, "cartridge", id, "status"] as const,
  graph: (id: string) => [ROOT, "graph", id] as const,
  dags: (id: string) => [ROOT, "dags", id] as const,
  dagSource: (id: string, dagId: string) => [ROOT, "dags", id, "source", dagId] as const,
  templates: () => [ROOT, "templates"] as const,
  systemInfo: () => [ROOT, "system-info"] as const,
  config: () => [ROOT, "config"] as const,
  entities: (id: string) => [ROOT, "entities", id] as const,
  preview: (layer: StudioLayer, id: string, dataset: string) => [ROOT, "preview", layer, id, dataset] as const,
};

export function useStudioCartridges() {
  return useQuery({ queryKey: studioKeys.cartridges(), queryFn: listStudioCartridges, staleTime: 60_000 });
}

export function useStudioManifest(id: string | null) {
  return useQuery({
    queryKey: studioKeys.cartridge(id ?? ""),
    queryFn: () => getStudioCartridge(id as string),
    enabled: Boolean(id),
    staleTime: 60_000,
  });
}

export function useCartridgeStatus(id: string | null) {
  return useQuery({
    queryKey: studioKeys.status(id ?? ""),
    queryFn: () => getCartridgeStatus(id as string),
    enabled: Boolean(id),
    staleTime: 30_000,
  });
}

export function useDagGraph(id: string | null) {
  return useQuery({
    queryKey: studioKeys.graph(id ?? ""),
    queryFn: () => getDagGraph(id as string),
    enabled: Boolean(id),
    staleTime: 30_000,
  });
}

export function useStudioDags(id: string | null) {
  return useQuery({
    queryKey: studioKeys.dags(id ?? ""),
    queryFn: () => listDags(id as string),
    enabled: Boolean(id),
    staleTime: 15_000,
    retry: false,
  });
}

export function useDagSource(id: string | null, dagId: string | null) {
  return useQuery({
    queryKey: studioKeys.dagSource(id ?? "", dagId ?? ""),
    queryFn: () => getDagSource(id as string, dagId as string),
    enabled: Boolean(id && dagId),
    staleTime: 15_000,
  });
}

export function useDagTemplates() {
  return useQuery({ queryKey: studioKeys.templates(), queryFn: listTemplates, staleTime: 5 * 60_000 });
}

export function useSystemInfo() {
  return useQuery({ queryKey: studioKeys.systemInfo(), queryFn: getSystemInfo, staleTime: 5 * 60_000 });
}

export function useRuntimeConfig() {
  return useQuery({ queryKey: studioKeys.config(), queryFn: getRuntimeConfig, staleTime: 5 * 60_000 });
}

export function useStudioEntities(id: string | null) {
  return useQuery({
    queryKey: studioKeys.entities(id ?? ""),
    queryFn: () => listStudioEntities(id as string),
    enabled: Boolean(id),
    staleTime: 30_000,
  });
}

export function useLayerPreview(layer: StudioLayer, id: string | null, dataset: string | null) {
  return useQuery({
    queryKey: studioKeys.preview(layer, id ?? "", dataset ?? ""),
    queryFn: () => getLayerPreview(layer, id as string, dataset as string),
    enabled: Boolean(id && dataset),
    staleTime: 30_000,
  });
}

export function useCreateCartridge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateCartridgeInput) => createCartridge(input),
    onSuccess: () => client.invalidateQueries({ queryKey: studioKeys.cartridges() }),
  });
}

export function useImportCartridge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => importCartridge(file),
    onSuccess: () => client.invalidateQueries({ queryKey: [ROOT] }),
  });
}

export function useDeployDag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: DeployDagInput) => deployDag(input),
    onSuccess: (_result, input) => {
      client.invalidateQueries({ queryKey: studioKeys.dags(input.cartridge) });
      client.invalidateQueries({ queryKey: studioKeys.graph(input.cartridge) });
    },
  });
}

export function useRenameDag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: renameDag,
    onSuccess: (_result, input) => client.invalidateQueries({ queryKey: studioKeys.dags(input.cartridge) }),
  });
}

export function useDeleteDag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { cartridge: string; dagId: string }) => deleteDag(input.cartridge, input.dagId),
    onSuccess: (_result, input) => client.invalidateQueries({ queryKey: studioKeys.dags(input.cartridge) }),
  });
}

export function usePublishSuperset() {
  return useMutation({
    mutationFn: (input: { cartridge: string; tableName: string }) =>
      publishSupersetDataset(input.cartridge, input.tableName),
  });
}

function useEntityInvalidation() {
  const client = useQueryClient();
  return (cartridge: string) => {
    client.invalidateQueries({ queryKey: studioKeys.entities(cartridge) });
    client.invalidateQueries({ queryKey: studioKeys.cartridge(cartridge) });
    client.invalidateQueries({ queryKey: studioKeys.graph(cartridge) });
  };
}

export function useUpdateEntity() {
  const invalidate = useEntityInvalidation();
  return useMutation({
    mutationFn: (input: { cartridge: string; entity: string; patch: EntityPatch }) =>
      updateEntity(input.cartridge, input.entity, input.patch),
    onSuccess: (_result, input) => invalidate(input.cartridge),
  });
}

export function useRenameEntity() {
  const invalidate = useEntityInvalidation();
  return useMutation({
    mutationFn: (input: { cartridge: string; entity: string; newName: string }) =>
      renameEntity(input.cartridge, input.entity, input.newName),
    onSuccess: (_result, input) => invalidate(input.cartridge),
  });
}

export function useCreateEntity() {
  const invalidate = useEntityInvalidation();
  return useMutation({
    mutationFn: (input: CreateEntityInput) => createEntity(input),
    onSuccess: (_result, input) => invalidate(input.cartridge),
  });
}

export function useUploadEntitySpec() {
  const invalidate = useEntityInvalidation();
  return useMutation({
    mutationFn: (input: { cartridge: string; file: File }) => uploadEntitySpec(input.cartridge, input.file),
    onSuccess: (_result, input) => invalidate(input.cartridge),
  });
}

export function useIntrospectSource() {
  return useMutation({ mutationFn: (cartridge: string) => introspectSource(cartridge) });
}

function useDatasetInvalidation() {
  const client = useQueryClient();
  return () => {
    client.invalidateQueries({ queryKey: ["monitor", "datasets"] });
    client.invalidateQueries({ queryKey: ["monitor", "dataset"] });
    client.invalidateQueries({ queryKey: [ROOT, "preview"] });
    client.invalidateQueries({ queryKey: [ROOT, "graph"] });
  };
}

export function useSaveDataset() {
  const invalidate = useDatasetInvalidation();
  return useMutation({ mutationFn: (input: SaveDatasetInput) => saveDataset(input), onSuccess: invalidate });
}

export function useRefreshDataset() {
  const invalidate = useDatasetInvalidation();
  return useMutation({ mutationFn: (name: string) => refreshDataset(name), onSuccess: invalidate });
}

export function useDeleteDataset() {
  const invalidate = useDatasetInvalidation();
  return useMutation({ mutationFn: (name: string) => deleteDataset(name), onSuccess: invalidate });
}
