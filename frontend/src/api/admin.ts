import { API_BASE_URL } from "./config";

type RequestOptions = {
  method?: string;
  body?: unknown;
};

export type MetadataTable = {
  table_name: string;
  display_name?: string | null;
  description?: string | null;
  domain?: string | null;
  row_count?: number;
};

export type MetadataColumn = {
  column_name: string;
  data_type: string;
  description?: string | null;
  is_dimension?: boolean;
  is_metric?: boolean;
  sample_values?: string | null;
};

export type Metric = {
  name: string;
  label: string;
  expression: string;
  description: string | null;
  default_time_column: string | null;
  allowed_dimensions: string[];
  enabled: boolean;
};

export type Alias = {
  id: number;
  table_name: string;
  column_name: string;
  alias: string;
};

export type VerifiedQuery = {
  id: string;
  question: string;
  sql: string;
  tags: string[];
  verified_by: string;
  enabled: boolean;
};

export type AnalysisSpace = {
  name: string;
  datasource: string;
  tables: string[];
  allowed_tables: string[];
  enabled_metrics: string[];
  allowed_operations: string[];
};

export type Relationship = {
  id: number;
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relationship_type: string;
  source: string;
  confidence: number;
  fanout_risk: string;
  description: string | null;
};

export type VectorIndexStatus = {
  vector_enabled: boolean;
  status: string;
  embedding_model?: string | null;
  embedding_dimension?: number | null;
  built_at?: string | null;
  asset_counts?: Record<string, number>;
  stale_reason?: string | null;
  qdrant_url?: string;
  qdrant_collection_prefix?: string;
};

export type VectorIndexBuildResult = {
  embedding_model: string;
  embedding_dimension: number;
  built_at: string;
  asset_counts: Record<string, number>;
};

export type MetricPayload = {
  name?: string;
  label?: string;
  expression?: string;
  description?: string | null;
  default_time_column?: string | null;
  allowed_dimensions?: string[];
  enabled?: boolean;
};

export type AliasPayload = {
  table_name: string;
  column_name: string;
  alias: string;
};

export type VerifiedQueryPayload = {
  query_id?: string;
  question?: string;
  sql?: string;
  tags?: string[];
  verified_by?: string;
  enabled?: boolean;
};

export type AnalysisSpacePayload = {
  tables?: string[];
  enabled_metrics?: string[];
  allowed_operations?: string[];
};

export type RelationshipPayload = {
  confidence?: number;
  fanout_risk?: string;
  source?: string;
  description?: string | null;
};

export async function listTables() {
  return requestJson<MetadataTable[]>("/api/metadata/tables");
}

export async function listColumns(tableName: string) {
  return requestJson<MetadataColumn[]>(`/api/metadata/tables/${encodeURIComponent(tableName)}/columns`);
}

export async function listMetrics() {
  return requestJson<Metric[]>("/api/metadata/metrics");
}

export async function createMetric(payload: MetricPayload) {
  return requestJson<Metric>("/api/metadata/metrics", { method: "POST", body: payload });
}

export async function updateMetric(name: string, payload: MetricPayload) {
  return requestJson<Metric>(`/api/metadata/metrics/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: payload,
  });
}

export async function toggleMetric(name: string) {
  return requestJson<Metric>(`/api/metadata/metrics/${encodeURIComponent(name)}/toggle`, {
    method: "PATCH",
  });
}

export async function listAliases(tableName?: string) {
  const query = tableName ? `?table_name=${encodeURIComponent(tableName)}` : "";
  return requestJson<Alias[]>(`/api/metadata/aliases${query}`);
}

export async function createAlias(payload: AliasPayload) {
  return requestJson<Alias>("/api/metadata/aliases", { method: "POST", body: payload });
}

export async function deleteAlias(id: number) {
  await requestJson<void>(`/api/metadata/aliases/${id}`, { method: "DELETE" });
}

export async function listVerifiedQueries() {
  return requestJson<VerifiedQuery[]>("/api/metadata/verified-queries");
}

export async function createVerifiedQuery(payload: VerifiedQueryPayload) {
  return requestJson<VerifiedQuery>("/api/metadata/verified-queries", {
    method: "POST",
    body: payload,
  });
}

export async function updateVerifiedQuery(id: string, payload: VerifiedQueryPayload) {
  return requestJson<VerifiedQuery>(`/api/metadata/verified-queries/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: payload,
  });
}

export async function toggleVerifiedQuery(id: string) {
  return requestJson<VerifiedQuery>(`/api/metadata/verified-queries/${encodeURIComponent(id)}/toggle`, {
    method: "PATCH",
  });
}

export async function getAnalysisSpace() {
  return requestJson<AnalysisSpace>("/api/metadata/analysis-space");
}

export async function updateAnalysisSpace(payload: AnalysisSpacePayload) {
  return requestJson<AnalysisSpace>("/api/metadata/analysis-space", {
    method: "PUT",
    body: payload,
  });
}

export async function listRelationships() {
  return requestJson<Relationship[]>("/api/metadata/relationships");
}

export async function updateRelationship(id: number, payload: RelationshipPayload) {
  return requestJson<Relationship>(`/api/metadata/relationships/${id}`, {
    method: "PUT",
    body: payload,
  });
}

export async function getVectorIndexStatus() {
  return requestJson<VectorIndexStatus>("/api/metadata/vector/status");
}

export async function rebuildVectorIndex() {
  return requestJson<VectorIndexBuildResult>("/api/metadata/vector/rebuild", {
    method: "POST",
  });
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers: options.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || `Request failed with status ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function readErrorDetail(response: Response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    return JSON.stringify(payload.detail ?? payload);
  } catch {
    return response.statusText;
  }
}
