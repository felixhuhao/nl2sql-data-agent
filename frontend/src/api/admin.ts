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
  engine?: string;
  partition_key?: string;
  sorting_key?: string;
};

export type MetadataColumn = {
  column_name: string;
  data_type: string;
  description?: string | null;
  nullable?: boolean;
  is_dimension?: boolean;
  is_metric?: boolean;
  sample_values?: string | null;
  is_partition_key?: boolean;
  is_sorting_key?: boolean;
  is_primary_key?: boolean;
  low_cardinality?: boolean;
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
  vector_mode?: string;
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

export async function listTables(datasource?: string) {
  return requestJson<MetadataTable[]>(withDatasource("/api/metadata/tables", datasource));
}

export async function listColumns(tableName: string, datasource?: string) {
  return requestJson<MetadataColumn[]>(
    withDatasource(`/api/metadata/tables/${encodeURIComponent(tableName)}/columns`, datasource),
  );
}

export async function listMetrics(datasource?: string) {
  return requestJson<Metric[]>(withDatasource("/api/metadata/metrics", datasource));
}

export async function createMetric(payload: MetricPayload, datasource?: string) {
  return requestJson<Metric>(withDatasource("/api/metadata/metrics", datasource), { method: "POST", body: payload });
}

export async function updateMetric(name: string, payload: MetricPayload, datasource?: string) {
  return requestJson<Metric>(withDatasource(`/api/metadata/metrics/${encodeURIComponent(name)}`, datasource), {
    method: "PUT",
    body: payload,
  });
}

export async function toggleMetric(name: string, datasource?: string) {
  return requestJson<Metric>(
    withDatasource(`/api/metadata/metrics/${encodeURIComponent(name)}/toggle`, datasource),
    {
      method: "PATCH",
    },
  );
}

export async function listAliases(tableName?: string, datasource?: string) {
  const params = new URLSearchParams();
  if (tableName) {
    params.set("table_name", tableName);
  }
  if (datasource) {
    params.set("datasource", datasource);
  }
  const query = params.toString();
  return requestJson<Alias[]>(`/api/metadata/aliases${query ? `?${query}` : ""}`);
}

export async function createAlias(payload: AliasPayload, datasource?: string) {
  return requestJson<Alias>(withDatasource("/api/metadata/aliases", datasource), { method: "POST", body: payload });
}

export async function deleteAlias(id: number, datasource?: string) {
  await requestJson<void>(withDatasource(`/api/metadata/aliases/${id}`, datasource), { method: "DELETE" });
}

export async function listVerifiedQueries(datasource?: string) {
  return requestJson<VerifiedQuery[]>(withDatasource("/api/metadata/verified-queries", datasource));
}

export async function createVerifiedQuery(payload: VerifiedQueryPayload, datasource?: string) {
  return requestJson<VerifiedQuery>(withDatasource("/api/metadata/verified-queries", datasource), {
    method: "POST",
    body: payload,
  });
}

export async function updateVerifiedQuery(id: string, payload: VerifiedQueryPayload, datasource?: string) {
  return requestJson<VerifiedQuery>(
    withDatasource(`/api/metadata/verified-queries/${encodeURIComponent(id)}`, datasource),
    {
      method: "PUT",
      body: payload,
    },
  );
}

export async function toggleVerifiedQuery(id: string, datasource?: string) {
  return requestJson<VerifiedQuery>(
    withDatasource(`/api/metadata/verified-queries/${encodeURIComponent(id)}/toggle`, datasource),
    {
      method: "PATCH",
    },
  );
}

export async function getAnalysisSpace(datasource?: string) {
  return requestJson<AnalysisSpace>(withDatasource("/api/metadata/analysis-space", datasource));
}

export async function updateAnalysisSpace(payload: AnalysisSpacePayload, datasource?: string) {
  return requestJson<AnalysisSpace>(withDatasource("/api/metadata/analysis-space", datasource), {
    method: "PUT",
    body: payload,
  });
}

export async function listRelationships(datasource?: string) {
  return requestJson<Relationship[]>(withDatasource("/api/metadata/relationships", datasource));
}

export async function updateRelationship(id: number, payload: RelationshipPayload, datasource?: string) {
  return requestJson<Relationship>(withDatasource(`/api/metadata/relationships/${id}`, datasource), {
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

function withDatasource(path: string, datasource?: string) {
  if (!datasource) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}datasource=${encodeURIComponent(datasource)}`;
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
