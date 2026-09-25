"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { listAgentRuns, type AgentRunRecord } from "@/lib/admin-surfaces";
import { getControlRoomAgentsOps } from "@/lib/control-room/client";
import { listCartridgeAgents, wisdomBitMonitorsFrom } from "@/lib/control-room/wisdom-bit-monitors";

const RECENT_RUNS = 5;

export function useWisdomBitMonitors(cartridgeId: string, wisdomBitPrefix: string, enabled = true) {
  const agents = useQuery({
    queryKey: ["agents", "cartridge", cartridgeId],
    queryFn: () => listCartridgeAgents(cartridgeId),
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const monitors = useMemo(
    () => wisdomBitMonitorsFrom(agents.data ?? [], wisdomBitPrefix),
    [agents.data, wisdomBitPrefix],
  );
  const runQueries = useQueries({
    queries: monitors.map((monitor) => ({
      queryKey: ["agents", monitor.id, "runs", RECENT_RUNS],
      queryFn: () => listAgentRuns(monitor.id, RECENT_RUNS),
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    })),
  });
  const ops = useQuery({
    queryKey: ["control-room", "agents-ops", 50],
    queryFn: () => getControlRoomAgentsOps(50),
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const runsByAgent = useMemo(() => {
    const byAgent: Record<string, { runs: AgentRunRecord[]; loading: boolean; failed: boolean }> = {};
    monitors.forEach((monitor, index) => {
      const query = runQueries[index];
      byAgent[monitor.id] = {
        runs: query?.data ?? [],
        loading: Boolean(query?.isPending),
        failed: Boolean(query?.isError),
      };
    });
    return byAgent;
  }, [monitors, runQueries]);

  return { agents, monitors, runsByAgent, ops };
}
