import { api } from "@/lib/api";
import type {
  DataRow,
  DatasetDetail,
  DatasetLineageRow,
  DatasetSummary,
  FreshnessEntity,
  JobLogLine,
  JobRun,
  LineagePayload,
  PipelineEntity,
  SemanticPayload,
  SourceSchemaPayload,
  VaultConnection,
  VaultSecret,
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

export async function listDatasets(): Promise<DatasetSummary[]> {
  const { data } = await api.get<{ datasets?: DatasetSummary[]; result?: DatasetSummary[] }>("/datasets");
  return data.datasets ?? data.result ?? [];
}

export async function getDatasetDetail(name: string): Promise<DatasetDetail> {
  const { data } = await api.get<DatasetDetail>(`/datasets/${encodeURIComponent(name)}/schema`);
  return data;
}

export async function getDatasetPreview(name: string, limit = 20): Promise<DataRow[]> {
  const { data } = await api.get<{ rows?: DataRow[]; data?: DataRow[]; result?: DataRow[] }>(
    `/datasets/${encodeURIComponent(name)}/data?limit=${limit}`,
  );
  return data.rows ?? data.data ?? data.result ?? [];
}

export async function getDatasetLineage(name: string): Promise<DatasetLineageRow[]> {
  const { data } = await api.get<{ lineage?: DatasetLineageRow[]; result?: DatasetLineageRow[] }>(
    `/api/datasets/${encodeURIComponent(name)}/lineage`,
  );
  return data.lineage ?? data.result ?? [];
}

export async function listSources(): Promise<string[]> {
  const { data } = await api.get<{ sources?: string[]; result?: string[] }>("/api/sources");
  return data.sources ?? data.result ?? [];
}

export async function getSourceSchema(source: string): Promise<SourceSchemaPayload> {
  const { data } = await api.get<SourceSchemaPayload>(
    `/api/schema?source=${encodeURIComponent(source)}`,
  );
  return data;
}

export async function getLineage(cartridge?: string): Promise<LineagePayload> {
  const query = cartridge ? `?cartridge=${encodeURIComponent(cartridge)}` : "";
  const { data } = await api.get<LineagePayload>(`/api/lineage${query}`);
  return {
    nodes: data.nodes ?? [],
    edges: data.edges ?? [],
  };
}

export async function listVaultConnections(cartridge: string): Promise<VaultConnection[]> {
  const { data } = await api.get<{ connections?: VaultConnection[] }>(
    `/api/vault/connections/${encodeURIComponent(cartridge)}`,
  );
  return data.connections ?? [];
}

export async function listVaultSecrets(scope: string): Promise<VaultSecret[]> {
  const { data } = await api.get<{ secrets?: VaultSecret[] }>(
    `/api/vault/secrets/${encodeURIComponent(scope)}`,
  );
  return data.secrets ?? [];
}
