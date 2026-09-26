"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getMeAccess } from "@/lib/admin-surfaces";
import { createItemDecision, getControlRoomDashboard, listAlerts, markAlertFalsePositive } from "@/lib/control-room/client";

import {
  SAP_B1_CARTRIDGE,
  addSapB1Recipient,
  getLoadReconciliation,
  getSapB1BusinessParameters,
  getSapB1Indicators,
  getSapB1Mapping,
  getSapB1Overview,
  getSapB1Recipients,
  getSapB1View,
  removeSapB1Recipient,
  saveSapB1BusinessParameters,
  uploadSapB1FinanceRun,
} from "./client";
import type { SapB1ViewName } from "./types";

const ROOT = "sap-b1";
const READ = { staleTime: 60_000, refetchOnWindowFocus: false } as const;

export function useSapB1Access() {
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const permissions = new Set(access.data?.permissions ?? []);
  const installed = (access.data?.cartridges?.allowed ?? []).some((item) => item.cartridge_id === SAP_B1_CARTRIDGE);
  return {
    access,
    installed,
    canWrite: permissions.has("control_room.write"),
    canReadAgents: access.data ? permissions.has("agents.read") : null,
  };
}

export function useSapB1Overview() {
  return useQuery({ queryKey: [ROOT, "overview"], queryFn: getSapB1Overview, ...READ });
}

export function useSapB1Mapping(enabled = true) {
  return useQuery({ queryKey: [ROOT, "mapping"], queryFn: getSapB1Mapping, enabled, ...READ });
}

export function useSapB1LoadReconciliation(enabled = true) {
  return useQuery({ queryKey: [ROOT, "load-reconciliation"], queryFn: getLoadReconciliation, enabled, ...READ });
}

export function useSapB1Indicators(enabled = true) {
  return useQuery({ queryKey: [ROOT, "indicators"], queryFn: getSapB1Indicators, enabled, staleTime: 5 * 60_000, refetchOnWindowFocus: false });
}

export function useSapB1View<V extends SapB1ViewName>(view: V, enabled = true) {
  return useQuery({ queryKey: [ROOT, "view", view], queryFn: () => getSapB1View(view), enabled, ...READ });
}

export function useSapB1BusinessParameters(enabled = true) {
  return useQuery({ queryKey: [ROOT, "business-parameters"], queryFn: getSapB1BusinessParameters, enabled, staleTime: 0, refetchOnWindowFocus: false });
}

export function useSaveSapB1BusinessParameters() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveSapB1BusinessParameters,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [ROOT, "business-parameters"] });
      void queryClient.invalidateQueries({ queryKey: [ROOT, "overview"] });
    },
  });
}

export function useUploadSapB1FinanceRun() {
  return useMutation({ mutationFn: uploadSapB1FinanceRun });
}

export function useSapB1Recipients(enabled = true) {
  return useQuery({ queryKey: [ROOT, "recipients"], queryFn: getSapB1Recipients, enabled, staleTime: 0, refetchOnWindowFocus: false });
}

function useRecipientMutation<T>(mutationFn: (email: string) => Promise<T>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [ROOT, "recipients"] });
      void queryClient.invalidateQueries({ queryKey: [ROOT, "overview"] });
    },
  });
}

export function useAddSapB1Recipient() {
  return useRecipientMutation(addSapB1Recipient);
}

export function useRemoveSapB1Recipient() {
  return useRecipientMutation(removeSapB1Recipient);
}

const ALERTS_KEY = ["control-room", "alerts"] as const;
const DASHBOARD_KEY = ["control-room", "dashboard"] as const;
const LIVE = { staleTime: 0, refetchOnWindowFocus: false } as const;

export function useSapB1AgentAlerts() {
  const alerts = useQuery({ queryKey: ALERTS_KEY, queryFn: listAlerts, ...LIVE });
  const dashboard = useQuery({ queryKey: DASHBOARD_KEY, queryFn: getControlRoomDashboard, ...LIVE });
  return { alerts, dashboard };
}

function useAlertMutation<T, V>(mutationFn: (variables: V) => Promise<T>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ALERTS_KEY });
      void queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY });
      void queryClient.invalidateQueries({ queryKey: [ROOT, "view", "sap_b1_learning_kpis"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });
}

export function useRecordSapB1AlertDecision() {
  return useAlertMutation(createItemDecision);
}

export function useMarkSapB1AlertFalsePositive() {
  return useAlertMutation(({ itemId, note }: { itemId: string; note?: string }) => markAlertFalsePositive(itemId, note));
}
