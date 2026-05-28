import { api } from "@/lib/api";
import type {
  FreshnessEntity,
  JobLogLine,
  JobRun,
  LineagePayload,
  PipelineEntity,
  SemanticPayload,
} from "./types";

export async function listJobs(limit = 100): Promise<JobRun[]> {
  const { data } = await api.get<{ jobs: JobRun[] }>(`/api/jobs?limit=${limit}`);
  return data.jobs ?? [];
}

export async function getJob(jobId: string): Promise<JobRun> {
  const { data } = await api.get<JobRun | { job: JobRun }>(`/api/jobs/${encodeURIComponent(jobId)}`);
  return "job" in data ? data.job : data;
}

export async function getJobLogs(jobId: string, limit = 500): Promise<JobLogLine[]> {
  const { data } = await api.get<{ logs: JobLogLine[] }>(
    `/api/jobs/${encodeURIComponent(jobId)}/logs?limit=${limit}`,
  );
  return data.logs ?? [];
}

export async function getPipeline(cartridge: string): Promise<PipelineEntity[]> {
  const { data } = await api.get<{ pipeline: PipelineEntity[] }>(
    `/api/pipeline?cartridge=${encodeURIComponent(cartridge)}`,
  );
  return data.pipeline ?? [];
}

export async function getFreshness(cartridge: string): Promise<FreshnessEntity[]> {
  const { data } = await api.get<{ entities: FreshnessEntity[] }>(
    `/api/freshness/${encodeURIComponent(cartridge)}`,
  );
  return data.entities ?? [];
}

export async function getSemantic(cartridge: string): Promise<SemanticPayload> {
  const { data } = await api.get<SemanticPayload>(
    `/api/semantic?cartridge=${encodeURIComponent(cartridge)}`,
  );
  return data;
}

export async function getLineage(cartridge?: string): Promise<LineagePayload> {
  const suffix = cartridge ? `?cartridge=${encodeURIComponent(cartridge)}` : "";
  const { data } = await api.get<LineagePayload>(`/api/lineage${suffix}`);
  return {
    nodes: data.nodes ?? [],
    edges: data.edges ?? [],
  };
}
