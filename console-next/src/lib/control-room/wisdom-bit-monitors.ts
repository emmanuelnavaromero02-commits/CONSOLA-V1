import { api } from "@/lib/api";
import type { AgentRecord, AgentsResponse } from "@/lib/admin-surfaces";

export interface WisdomBitMonitor {
  id: string;
  name: string;
  slug: string | null;
  cartridgeId: string | null;
  wisdomBitId: string;
  description: string | null;
  active: boolean;
  cron: string | null;
  timeZone: string | null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function cartridgeAgentsPath(cartridgeId: string): string {
  const params = new URLSearchParams({ cartridge_id: cartridgeId, include_inactive: "true" });
  return `/api/agents?${params.toString()}`;
}

export async function listCartridgeAgents(cartridgeId: string): Promise<AgentRecord[]> {
  const { data } = await api.get<Partial<AgentsResponse>>(cartridgeAgentsPath(cartridgeId));
  return Array.isArray(data?.agents) ? data.agents : [];
}

export function wisdomBitMonitorsFrom(agents: AgentRecord[], wisdomBitPrefix: string): WisdomBitMonitor[] {
  const monitors: WisdomBitMonitor[] = [];
  for (const agent of agents) {
    const extra = record(agent.extra);
    const monitor = record(extra.monitor);
    const schedule = record(extra.schedule ?? monitor.schedule);
    const wisdomBitId = text(monitor.wisdom_bit_id);
    if (!wisdomBitId || !wisdomBitId.startsWith(wisdomBitPrefix)) continue;
    monitors.push({
      id: agent.id,
      name: agent.name,
      slug: agent.slug ?? null,
      cartridgeId: agent.cartridge_id ?? null,
      wisdomBitId,
      description: text(agent.description),
      active: agent.is_active !== false,
      cron: text(schedule.cron),
      timeZone: text(schedule.tz),
    });
  }
  return monitors.sort((a, b) => (dailyMinutes(a.cron) ?? 9999) - (dailyMinutes(b.cron) ?? 9999));
}

const DAILY_CRON = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/;

export function dailyParts(cron: string | null | undefined): { hour: number; minute: number } | null {
  const match = DAILY_CRON.exec((cron ?? "").trim());
  if (!match) return null;
  const minute = Number(match[1]);
  const hour = Number(match[2]);
  return minute <= 59 && hour <= 23 ? { hour, minute } : null;
}

function dailyMinutes(cron: string | null | undefined): number | null {
  const parts = dailyParts(cron);
  return parts ? parts.hour * 60 + parts.minute : null;
}

export function scheduleLabel(cron: string | null | undefined): string | null {
  const parts = dailyParts(cron);
  if (!parts) return cron?.trim() || null;
  return `${String(parts.hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}`;
}

function zoneOffsetMs(moment: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(moment));
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  const wall = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return wall - Math.floor(moment / 1000) * 1000;
}

export function nextDailyRun(cron: string | null | undefined, timeZone: string | null | undefined, now = new Date()): Date | null {
  const parts = dailyParts(cron);
  if (!parts) return null;
  const zone = timeZone || "UTC";
  try {
    const nowMs = now.getTime();
    const wallNow = nowMs + zoneOffsetMs(nowMs, zone);
    const today = new Date(wallNow);
    let wallTarget = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), parts.hour, parts.minute);
    if (wallTarget <= wallNow) wallTarget += 86_400_000;
    let utc = wallTarget - zoneOffsetMs(wallTarget, zone);
    utc = wallTarget - zoneOffsetMs(utc, zone);
    return new Date(utc);
  } catch {
    return null;
  }
}
