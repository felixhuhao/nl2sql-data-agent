<script setup lang="ts">
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import Admin from "./Admin.vue";
import { API_BASE_URL } from "./api/config";
import { listDatasources, type DatasourceInfo } from "./api/datasources";

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const CHART_PALETTE = ["#c8442f", "#2f6f5e", "#9c6b16", "#3d6b8a", "#7d6a9c", "#b06a3d", "#4f8a6b", "#8a5a3d"];
echarts.registerTheme("ledger", {
  color: CHART_PALETTE,
  backgroundColor: "transparent",
  textStyle: {
    fontFamily: '"Hanken Grotesk", "Noto Sans SC", system-ui, sans-serif',
    color: "#5c5447",
  },
  title: { textStyle: { color: "#2b2620" } },
  legend: { textStyle: { color: "#5c5447" }, itemWidth: 14, itemHeight: 9 },
  categoryAxis: {
    axisLine: { lineStyle: { color: "rgba(43,38,32,0.22)" } },
    axisTick: { lineStyle: { color: "rgba(43,38,32,0.22)" } },
    axisLabel: { color: "#8c8474" },
    splitLine: { show: false, lineStyle: { color: "rgba(43,38,32,0.08)" } },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: "#8c8474" },
    splitLine: { lineStyle: { color: "rgba(43,38,32,0.08)" } },
  },
  tooltip: {
    backgroundColor: "#fffdf8",
    borderColor: "rgba(43,38,32,0.14)",
    borderWidth: 1,
    textStyle: { color: "#2b2620" },
    extraCssText: "box-shadow: 0 14px 38px -16px rgba(43,38,32,0.24); border-radius: 10px;",
  },
});

const allWorkflowSteps = [
  { id: "datasource_selected", label: "选择数据源" },
  { id: "intent_guard", label: "意图检查" },
  { id: "retrieve_context", label: "检索上下文" },
  { id: "build_context", label: "构建上下文" },
  { id: "olap_detected", label: "分析意图" },
  { id: "generate_sql", label: "生成 SQL" },
  { id: "sql_guard", label: "SQL Guard" },
  { id: "conversation_filter_verify", label: "保留过滤" },
  { id: "semantic_guard", label: "语义校验" },
  { id: "repair_sql", label: "SQL 修复" },
  { id: "execute", label: "执行查询" },
  { id: "explain_plan", label: "性能解释" },
  { id: "summarize", label: "生成回答" },
  { id: "recommend_chart", label: "推荐图表" },
] as const;

type WorkflowStepId = (typeof allWorkflowSteps)[number]["id"];
type StepStatus = "pending" | "running" | "completed" | "error";
type GuardResult = {
  allowed?: boolean;
  stage?: string;
  normalized_sql?: string | null;
  reason?: string | null;
  warnings?: string[];
};
type Explainability = {
  matched_tables?: string[];
  matched_columns?: string[];
  join_paths?: Record<string, any>[];
  date_interpretation?: Record<string, unknown>;
  guard_result?: GuardResult | null;
};
type RetrievalValueHit = {
  table_name?: string;
  column_name?: string;
  matched_value?: string;
  source?: string;
  score?: number;
};
type RetrievalMeta = {
  vector_used?: boolean;
  index_status?: string | null;
  stale_reason?: string | null;
  value_hits?: RetrievalValueHit[];
  retrieval_sources?: Record<string, string[]>;
};
type RetrievalSourceDisplay = {
  raw: string;
  label: string;
  className: string;
};
type RetrievalSourceItem = {
  assetKey: string;
  assetLabel: string;
  assetTypeLabel: string;
  sources: RetrievalSourceDisplay[];
};
type RetrievalSourceGroup = {
  id: string;
  label: string;
  items: RetrievalSourceItem[];
};
type RetrievalSourceStat = {
  id: string;
  label: string;
  count: number;
  className: string;
  title: string;
};
type RepairHistoryItem = {
  attempt?: number;
  original_sql?: string | null;
  repaired_sql?: string | null;
  error_stage?: string | null;
  error_kind?: string | null;
  error_reason?: string | null;
  normalized_sql?: string | null;
  succeeded?: boolean | null;
  final_stage?: string | null;
};
type ChartRecommendation = {
  chart_type?: string;
  x_column?: string | null;
  y_columns?: string[];
  reason?: string;
};
type PieDatum = {
  name: string;
  value: number;
};
type RuntimeStats = {
  execution_time_ms?: number;
  rows_read?: number;
  bytes_read?: number;
};
type GroundingWarning = {
  concept?: string;
  failure_kind?: string;
  sql_mapping?: string | null;
  message?: string;
  explanation?: string;
  refutation_confirmed?: boolean;
  refutation_reason?: string;
};
type HealthPayload = {
  status?: string;
  llm_provider?: string;
  semantic_guard?: string;
  semantic_verifier?: string;
};
type QueryDatasource = Pick<DatasourceInfo, "name" | "dialect" | "display_name">;

const fallbackDatasource: DatasourceInfo = {
  name: "duckdb_ecommerce",
  dialect: "duckdb",
  display_name: "DuckDB (本地)",
  status: "available",
};

const question = ref("查询最近30天每日销售额和订单数");
const dataSources = ref<DatasourceInfo[]>([fallbackDatasource]);
const selectedDatasourceName = ref(fallbackDatasource.name);
const datasourceLoadError = ref("");
const isLoadingDatasources = ref(false);
const isSubmitting = ref(false);
const errorMessage = ref("");
const errorStep = ref("");
const errorKind = ref<"blocked" | "failure">("failure");
const stepStates = ref(createStepStates());
const sql = ref("");
const summary = ref("");
const rows = ref<unknown[][]>([]);
const columns = ref<string[]>([]);
const explainability = ref<Explainability | null>(null);
const retrievalMeta = ref<RetrievalMeta | null>(null);
const planHints = ref<string[]>([]);
const runtimeStats = ref<RuntimeStats | null>(null);
const groundingWarnings = ref<GroundingWarning[]>([]);
const repairHistory = ref<RepairHistoryItem[]>([]);
const guardResult = ref<GuardResult | null>(null);
const chartRecommendation = ref<ChartRecommendation | null>(null);
const queryDatasource = ref<QueryDatasource | null>(null);
const queryElapsedMs = ref<number | null>(null);
const resultRowCount = ref<number | null>(null);
const sessionId = ref(createSessionId());
const isFollowUp = ref(false);
const changeKind = ref("none");
const chartContainer = ref<HTMLDivElement | null>(null);
const activeView = ref<"chat" | "admin">("chat");
const llmProvider = ref("");
const sqlCopied = ref(false);
const exampleQuestions = [
  "查询最近30天每日销售额和订单数",
  "各渠道本月销售额占比",
  "按地区拆分销售额",
  "销量最高的10个商品",
];
const sourceGroupOrder = ["table", "column", "metric", "verified_query", "other"];
const sourceGroupLabels: Record<string, string> = {
  table: "表",
  column: "字段",
  metric: "指标",
  verified_query: "验证查询",
  other: "其他",
};
const providerStatusLabel = computed(() => {
  if (llmProvider.value === "deepseek") {
    return "DeepSeek Agent Ready";
  }
  if (llmProvider.value === "mock") {
    return "Mock Agent Ready";
  }
  return "Agent Ready";
});
const currentDatasource = computed(
  () => dataSources.value.find((source) => source.name === selectedDatasourceName.value) ?? dataSources.value[0] ?? fallbackDatasource,
);
const resultDatasource = computed(() => queryDatasource.value ?? currentDatasource.value);
const datasourceStatusLabel = computed(() => {
  if (isLoadingDatasources.value) {
    return "加载中";
  }
  if (datasourceLoadError.value) {
    return "加载失败";
  }
  return `${dataSources.value.length} 个数据源`;
});
const formattedElapsedMs = computed(() => {
  if (queryElapsedMs.value === null) {
    return "-";
  }
  return formatDuration(queryElapsedMs.value);
});
const followUpLabel = computed(() => {
  const labels: Record<string, string> = {
    dimension: "维度下钻",
    filter: "值过滤",
    metric: "指标切换",
    time: "时间范围",
    none: "追问",
  };
  return labels[changeKind.value] ?? "追问";
});
const canSubmit = computed(() => question.value.trim().length > 0 && !isSubmitting.value);
const hasActivity = computed(
  () =>
    isSubmitting.value ||
    stepStates.value.some((step) => step.status !== "pending") ||
    Boolean(summary.value) ||
    Boolean(errorMessage.value),
);
const canRenderChart = computed(() => {
  const recommendation = chartRecommendation.value;
  return (
    ["bar", "line", "dual_axis", "pie"].includes(recommendation?.chart_type ?? "") &&
    Boolean(recommendation?.x_column) &&
    Boolean(recommendation?.y_columns?.length) &&
    rows.value.length > 0
  );
});
const retrievalSourceGroups = computed<RetrievalSourceGroup[]>(() => {
  const sourceMap = retrievalMeta.value?.retrieval_sources ?? {};
  const groups = createSourceGroups();

  Object.entries(sourceMap).forEach(([assetKey, sources]) => {
    if (!sources.length) {
      return;
    }
    const asset = formatRetrievalAsset(assetKey);
    groups[asset.type].items.push({
      assetKey,
      assetLabel: asset.label,
      assetTypeLabel: asset.typeLabel,
      sources: sources.map(formatRetrievalSource),
    });
  });

  return sourceGroupOrder.map((groupId) => groups[groupId]).filter((group) => group.items.length);
});
const retrievalSourceStats = computed<RetrievalSourceStat[]>(() => {
  const counts: Record<string, number> = {
    rule: 0,
    value: 0,
    vector: 0,
    other: 0,
  };
  const valueEvidence = new Set<string>();

  Object.values(retrievalMeta.value?.retrieval_sources ?? {}).forEach((sources) => {
    sources.forEach((source) => {
      const family = retrievalSourceFamily(source);
      if (family === "value") {
        valueEvidence.add(source);
        return;
      }
      counts[family] += 1;
    });
  });
  counts.value = valueEvidence.size;

  return [
    {
      id: "rule",
      label: "规则命中",
      count: counts.rule,
      className: "source-rule",
      title: "字段别名、指标表达式、验证查询等确定性规则命中的证据数量",
    },
    {
      id: "value",
      label: "值命中",
      count: counts.value,
      className: "source-value",
      title: "问题中的业务取值命中的证据数量，例如 华东、天猫",
    },
    {
      id: "vector",
      label: "向量匹配",
      count: counts.vector,
      className: "source-vector",
      title: "语义相似度检索命中的证据数量",
    },
    {
      id: "other",
      label: "其他证据",
      count: counts.other,
      className: "",
      title: "其他来源的证据数量",
    },
  ].filter((stat) => stat.count > 0);
});
const hasRetrievalSources = computed(() => retrievalSourceGroups.value.length > 0);
let chartInstance: echarts.ECharts | null = null;

onMounted(() => {
  window.addEventListener("resize", resizeChart);
  void fetchAgentStatus();
  void fetchDatasources();
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
  disposeChart();
});

async function submitQuestion() {
  if (!canSubmit.value) {
    return;
  }

  isSubmitting.value = true;
  errorMessage.value = "";
  errorStep.value = "";
  errorKind.value = "failure";
  stepStates.value = createStepStates();
  setStepStatus("datasource_selected", "running");
  sql.value = "";
  summary.value = "";
  rows.value = [];
  columns.value = [];
  explainability.value = null;
  retrievalMeta.value = null;
  planHints.value = [];
  runtimeStats.value = null;
  groundingWarnings.value = [];
  repairHistory.value = [];
  guardResult.value = null;
  chartRecommendation.value = null;
  queryDatasource.value = currentDatasource.value;
  queryElapsedMs.value = null;
  resultRowCount.value = null;
  disposeChart();

  try {
    const response = await fetch(`${API_BASE_URL}/api/chat/query`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question: question.value.trim(),
        datasource: selectedDatasourceName.value,
        session_id: sessionId.value,
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    await readSseStream(response.body);
  } catch (error) {
    failStep(undefined);
    errorKind.value = "failure";
    errorMessage.value = error instanceof Error ? error.message : "请求失败";
  } finally {
    isSubmitting.value = false;
  }
}

function startNewConversation() {
  sessionId.value = createSessionId();
  isFollowUp.value = false;
  changeKind.value = "none";
  errorMessage.value = "";
  errorStep.value = "";
  question.value = "";
  sql.value = "";
  summary.value = "";
  rows.value = [];
  columns.value = [];
  explainability.value = null;
  retrievalMeta.value = null;
  planHints.value = [];
  runtimeStats.value = null;
  groundingWarnings.value = [];
  repairHistory.value = [];
  guardResult.value = null;
  chartRecommendation.value = null;
  queryDatasource.value = currentDatasource.value;
  queryElapsedMs.value = null;
  resultRowCount.value = null;
  stepStates.value = createStepStates();
  disposeChart();
}

function createSessionId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function fetchDatasources() {
  datasourceLoadError.value = "";
  isLoadingDatasources.value = true;
  try {
    const payload = await listDatasources();
    dataSources.value = payload.sources.length ? payload.sources : [fallbackDatasource];
    selectedDatasourceName.value =
      payload.default && dataSources.value.some((source) => source.name === payload.default)
        ? payload.default
        : dataSources.value[0].name;
  } catch (error) {
    dataSources.value = [fallbackDatasource];
    selectedDatasourceName.value = fallbackDatasource.name;
    datasourceLoadError.value = error instanceof Error ? error.message : "数据源加载失败";
  } finally {
    isLoadingDatasources.value = false;
  }
}

async function fetchAgentStatus() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) {
      return;
    }
    const payload = (await response.json()) as HealthPayload;
    llmProvider.value = (payload.llm_provider ?? "").toLowerCase();
  } catch {
    llmProvider.value = "";
  }
}

function createStepStates() {
  return visibleWorkflowSteps().map((step) => ({
    ...step,
    status: "pending" as StepStatus,
  }));
}

function visibleWorkflowSteps() {
  const datasource =
    dataSources.value.find((source) => source.name === selectedDatasourceName.value) ??
    fallbackDatasource;
  return allWorkflowSteps.filter((step) => step.id !== "explain_plan" || datasource.dialect === "clickhouse");
}

function setStepStatus(stepId: string, status: StepStatus) {
  const step = stepStates.value.find((item) => item.id === stepId);
  if (step) {
    step.status = status;
  }
}

function completeStep(stepId: WorkflowStepId) {
  const index = stepStates.value.findIndex((step) => step.id === stepId);
  if (index === -1) {
    return;
  }

  stepStates.value = stepStates.value.map((step, stepIndex) => {
    if (stepIndex < index && step.status === "pending") {
      return { ...step, status: "completed" };
    }
    return step;
  });
  stepStates.value[index].status = "completed";
  const nextStep = stepStates.value[index + 1];
  if (nextStep && nextStep.status === "pending") {
    nextStep.status = "running";
  }
}

function failStep(stepId: string | undefined) {
  const matchedIndex = stepStates.value.findIndex((step) => step.id === stepId);
  if (matchedIndex >= 0) {
    stepStates.value = stepStates.value.map((step, index) => {
      if (index === matchedIndex) {
        return { ...step, status: "error" };
      }
      if (index > matchedIndex) {
        return { ...step, status: "pending" };
      }
      return step;
    });
    return;
  }

  const runningIndex = stepStates.value.findIndex((step) => step.status === "running");
  if (runningIndex >= 0) {
    stepStates.value = stepStates.value.map((step, index) => {
      if (index === runningIndex) {
        return { ...step, status: "error" };
      }
      if (index > runningIndex) {
        return { ...step, status: "pending" };
      }
      return step;
    });
  }
}

function formatJoinPath(path: Record<string, any>) {
  const sourceTable = path.source_table ?? path.left_table;
  const targetTable = path.target_table ?? path.right_table;
  const sourceColumn = path.source_column ?? path.left_column;
  const targetColumn = path.target_column ?? path.right_column;

  if (sourceTable && targetTable && sourceColumn && targetColumn) {
    return `${sourceTable}.${sourceColumn} -> ${targetTable}.${targetColumn}`;
  }
  if (sourceTable && targetTable) {
    return `${sourceTable} -> ${targetTable}`;
  }
  return JSON.stringify(path);
}

function formatValueHit(hit: RetrievalValueHit) {
  const column = [hit.table_name, hit.column_name].filter(Boolean).join(".");
  if (hit.matched_value && column) {
    return `${hit.matched_value} -> ${column}`;
  }
  return hit.matched_value ?? column;
}

function retrievalModeLabel(vectorUsed?: boolean) {
  return vectorUsed ? "向量检索" : "规则检索";
}

function retrievalModeTitle(vectorUsed?: boolean) {
  return vectorUsed
    ? "本次元数据检索启用了向量语义检索，并与规则命中合并"
    : "本次元数据检索只使用规则命中";
}

function retrievalStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    ready: "索引就绪",
    stale: "索引需重建",
    missing: "索引缺失",
    disabled: "向量关闭",
    error: "索引异常",
  };
  return status ? (labels[status] ?? status) : "";
}

function retrievalStatusTitle(status?: string | null) {
  const labels: Record<string, string> = {
    ready: "向量索引可用",
    stale: "元数据已变化，向量索引需要重建",
    missing: "没有找到向量索引",
    disabled: "向量检索未启用",
    error: "向量检索状态检查失败",
  };
  return status ? (labels[status] ?? status) : "";
}

function formatDuration(milliseconds: number) {
  if (milliseconds >= 1000) {
    return `${(milliseconds / 1000).toFixed(2)}s`;
  }
  return `${milliseconds.toFixed(1)}ms`;
}

function retrievalSourceClass(source: string) {
  if (source.startsWith("value:")) {
    return "source-value";
  }
  if (source.startsWith("vector:")) {
    return "source-vector";
  }
  if (source.startsWith("rule:")) {
    return "source-rule";
  }
  return "";
}

function createSourceGroups() {
  return sourceGroupOrder.reduce<Record<string, RetrievalSourceGroup>>((groups, id) => {
    groups[id] = { id, label: sourceGroupLabels[id], items: [] };
    return groups;
  }, {});
}

function formatRetrievalAsset(assetKey: string) {
  const separatorIndex = assetKey.indexOf(":");
  const rawType = separatorIndex >= 0 ? assetKey.slice(0, separatorIndex) : "other";
  const type = sourceGroupLabels[rawType] ? rawType : "other";
  const label = separatorIndex >= 0 ? assetKey.slice(separatorIndex + 1) : assetKey;
  return {
    type,
    typeLabel: sourceGroupLabels[type],
    label,
  };
}

function formatRetrievalSource(source: string): RetrievalSourceDisplay {
  return {
    raw: source,
    label: formatRetrievalSourceLabel(source),
    className: retrievalSourceClass(source),
  };
}

function retrievalSourceFamily(source: string) {
  if (source.startsWith("rule:")) {
    return "rule";
  }
  if (source.startsWith("value:")) {
    return "value";
  }
  if (source.startsWith("vector:")) {
    return "vector";
  }
  return "other";
}

function formatRetrievalSourceLabel(source: string) {
  if (source.startsWith("value:")) {
    return `值 ${source.slice("value:".length)}`;
  }
  if (source.startsWith("vector:")) {
    return `向量 ${source.slice("vector:".length)}`;
  }
  if (!source.startsWith("rule:")) {
    return source;
  }

  const reason = source.slice("rule:".length);
  const separatorIndex = reason.indexOf(":");
  const kind = separatorIndex >= 0 ? reason.slice(0, separatorIndex) : reason;
  const detail = separatorIndex >= 0 ? reason.slice(separatorIndex + 1) : "";
  const detailText = detail ? ` ${detail}` : "";
  const ruleLabels: Record<string, string> = {
    alias: "别名",
    matched_alias: "别名命中",
    sample_value: "样本值",
    matched_sample: "样本命中",
    metric_expression: "指标表达式",
    metric_time_column: "指标时间列",
    metric_label: "指标名",
    metric_name: "指标名",
    metric_description: "指标描述",
    verified_query: "验证查询",
    verified_question_exact: "验证问法",
    verified_question_partial: "验证问法",
    table_name: "表名",
    table_display_name: "表展示名",
    table_domain: "业务域",
    table_description: "表描述",
    column_name: "字段名",
    column_description: "字段描述",
  };
  return `${ruleLabels[kind] ?? kind.replaceAll("_", " ")}${detailText}`;
}

async function readSseStream(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      handleSseChunk(chunk);
    }
  }

  if (buffer.trim()) {
    handleSseChunk(buffer);
  }
}

function handleSseChunk(chunk: string) {
  const event = chunk
    .split("\n")
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const data = chunk
    .split("\n")
    .find((line) => line.startsWith("data:"))
    ?.slice("data:".length)
    .trim();

  if (!event || !data) {
    return;
  }

  let payload: Record<string, any>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }

  if (event === "step") {
    completeStep(payload.step);
    if (payload.step === "datasource_selected") {
      queryDatasource.value = {
        name: payload.name ?? selectedDatasourceName.value,
        dialect: payload.dialect ?? currentDatasource.value?.dialect ?? "",
        display_name: payload.display_name ?? currentDatasource.value?.display_name ?? selectedDatasourceName.value,
      };
    }
    if (payload.step === "retrieve_context") {
      retrievalMeta.value = {
        vector_used: Boolean(payload.vector_used),
        index_status: payload.index_status ?? null,
        stale_reason: payload.stale_reason ?? null,
        value_hits: payload.value_hits ?? [],
        retrieval_sources: payload.retrieval_sources ?? {},
      };
    }
    if (payload.guard_result) {
      guardResult.value = payload.guard_result;
    }
    if (payload.repair_history) {
      repairHistory.value = payload.repair_history;
    }
    if (payload.step === "execute") {
      resultRowCount.value = payload.row_count ?? null;
      queryElapsedMs.value = payload.elapsed_ms ?? null;
    }
    if (payload.step === "generate_sql") {
      isFollowUp.value = Boolean(payload.is_follow_up);
      changeKind.value = payload.change_kind ?? "none";
    }
    if (payload.step === "explain_plan") {
      planHints.value = payload.plan_hints ?? [];
      runtimeStats.value = payload.runtime_stats ?? null;
    }
    if (payload.grounding_warnings) {
      groundingWarnings.value = payload.grounding_warnings;
    }
  }
  if (event === "session") {
    sessionId.value = payload.session_id ?? sessionId.value;
  }
  if (event === "done") {
    stepStates.value = stepStates.value.map((step) => ({ ...step, status: "completed" }));
    sql.value = payload.sql ?? "";
    summary.value = payload.summary ?? "";
    columns.value = payload.result?.columns ?? [];
    rows.value = payload.result?.rows ?? [];
    resultRowCount.value = payload.result?.row_count ?? rows.value.length;
    queryElapsedMs.value = payload.result?.elapsed_ms ?? queryElapsedMs.value;
    queryDatasource.value = payload.datasource ?? queryDatasource.value;
    chartRecommendation.value = payload.chart_recommendation ?? null;
    explainability.value = payload.explainability ?? null;
    planHints.value = payload.plan_hints ?? planHints.value;
    runtimeStats.value = payload.runtime_stats ?? runtimeStats.value;
    groundingWarnings.value = payload.grounding_warnings ?? groundingWarnings.value;
    repairHistory.value = payload.repair_history ?? repairHistory.value;
    guardResult.value = payload.explainability?.guard_result ?? guardResult.value;
    sessionId.value = payload.session_id ?? sessionId.value;
    isFollowUp.value = Boolean(payload.is_follow_up);
    changeKind.value = payload.change_kind ?? "none";
    void nextTick(renderChart);
  }
  if (event === "error") {
    failStep(payload.step);
    errorStep.value = payload.step ?? "";
    errorKind.value = payload.error_kind === "blocked" ? "blocked" : "failure";
    errorMessage.value = payload.reason ?? "请求失败";
    explainability.value = payload.explainability ?? null;
    planHints.value = payload.plan_hints ?? planHints.value;
    runtimeStats.value = payload.runtime_stats ?? runtimeStats.value;
    groundingWarnings.value = payload.grounding_warnings ?? groundingWarnings.value;
    repairHistory.value = payload.repair_history ?? repairHistory.value;
    guardResult.value = payload.explainability?.guard_result ?? guardResult.value;
    chartRecommendation.value = null;
    resultRowCount.value = null;
    queryElapsedMs.value = null;
    disposeChart();
  }
}

function renderChart() {
  if (!canRenderChart.value || !chartContainer.value || !chartRecommendation.value) {
    disposeChart();
    return;
  }

  const xColumn = chartRecommendation.value.x_column;
  const yColumns = chartRecommendation.value.y_columns ?? [];
  const chartType = chartRecommendation.value.chart_type;
  if (!xColumn) {
    disposeChart();
    return;
  }

  const xIndex = columns.value.indexOf(xColumn);
  const yIndexes = yColumns
    .map((column) => ({ column, index: columns.value.indexOf(column) }))
    .filter((item) => item.index >= 0);
  if (xIndex < 0 || !yIndexes.length) {
    disposeChart();
    return;
  }

  chartInstance ??= echarts.init(chartContainer.value, "ledger");

  if (chartType === "pie") {
    const yIndex = yIndexes[0].index;
    const pieData = pieSeriesData(xIndex, yIndex);
    const formatPieAsPercent = shouldFormatPieAsPercent(pieData);
    chartInstance.setOption({
      color: CHART_PALETTE,
      tooltip: {
        trigger: "item",
        formatter: (params: any) => formatPieTooltip(params, formatPieAsPercent),
      },
      legend: {
        top: 0,
        right: 0,
        orient: "vertical",
      },
      series: [
        {
          name: yIndexes[0].column,
          type: "pie",
          radius: ["34%", "68%"],
          center: ["42%", "54%"],
          data: pieData,
        },
      ],
    }, true);
    resizeChart();
    return;
  }

  if (chartType === "bar") {
    if (!isTopNRecommendation(chartRecommendation.value)) {
      chartInstance.setOption({
        color: CHART_PALETTE,
        grid: {
          top: 28,
          right: 20,
          bottom: 36,
          left: 56,
        },
        tooltip: {
          trigger: "axis",
        },
        legend: {
          top: 0,
          right: 0,
        },
        xAxis: {
          type: "category",
          data: rows.value.map((row) => String(row[xIndex] ?? "")),
        },
        yAxis: {
          type: "value",
        },
        series: yIndexes.map(({ column, index }) => ({
          name: column,
          type: "bar",
          barMaxWidth: 48,
          data: rows.value.map((row) => Number(row[index] ?? 0)),
        })),
      }, true);
      resizeChart();
      return;
    }

    chartInstance.setOption({
      color: CHART_PALETTE,
      grid: {
        top: 28,
        right: 20,
        bottom: 28,
        left: 96,
      },
      tooltip: {
        trigger: "axis",
      },
      legend: {
        top: 0,
        right: 0,
      },
      xAxis: {
        type: "value",
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: rows.value.map((row) => String(row[xIndex] ?? "")),
      },
      series: yIndexes.map(({ column, index }) => ({
        name: column,
        type: "bar",
        barMaxWidth: 32,
        data: rows.value.map((row) => Number(row[index] ?? 0)),
      })),
    }, true);
    resizeChart();
    return;
  }

  const lineIndexes = chartType === "dual_axis" ? yIndexes.slice(0, 2) : yIndexes;
  chartInstance.setOption({
    color: ["#235789", "#2e7d5b", "#b7791f"],
    grid: {
      top: 28,
      right: 20,
      bottom: 36,
      left: 56,
    },
    tooltip: {
      trigger: "axis",
    },
    legend: {
      top: 0,
      right: 0,
    },
    xAxis: {
      type: "category",
      data: rows.value.map((row) => String(row[xIndex] ?? "")),
    },
    yAxis: chartType === "dual_axis"
      ? [
          {
            type: "value",
            name: lineIndexes[0]?.column ?? "",
          },
          {
            type: "value",
            name: lineIndexes[1]?.column ?? "",
          },
        ]
      : {
          type: "value",
        },
    series: lineIndexes.map(({ column, index }, seriesIndex) => ({
      name: column,
      type: "line",
      smooth: true,
      symbolSize: 5,
      ...(chartType === "dual_axis" ? { yAxisIndex: Math.min(seriesIndex, 1) } : {}),
      data: rows.value.map((row) => Number(row[index] ?? 0)),
    })),
  }, true);
  resizeChart();
}

function isTopNRecommendation(recommendation: ChartRecommendation) {
  return (recommendation.reason ?? "").toLowerCase().includes("topn");
}

function pieSeriesData(xIndex: number, yIndex: number): PieDatum[] {
  const data = rows.value.map((row) => ({
    name: String(row[xIndex] ?? ""),
    value: Number(row[yIndex] ?? 0),
  }));
  if (data.length <= 8) {
    return data;
  }

  const visible = data.slice(0, 7);
  const otherValue = data.slice(7).reduce((total, item) => total + item.value, 0);
  return [...visible, { name: "其他", value: otherValue }];
}

function shouldFormatPieAsPercent(data: PieDatum[]) {
  if (!data.length) {
    return false;
  }
  const total = data.reduce((sum, item) => sum + item.value, 0);
  return total > 0 && total <= 1.01 && data.every((item) => item.value >= 0 && item.value <= 1);
}

function formatPieTooltip(params: any, asPercent: boolean) {
  const value = Number(params.value ?? 0);
  const displayValue = asPercent ? `${formatPercent(value * 100)}%` : value.toLocaleString();
  const slicePercent = Number(params.percent ?? 0);
  return `${params.marker ?? ""}${params.name ?? ""}<br/>${params.seriesName ?? "值"}: ${displayValue} (${formatPercent(slicePercent)}%)`;
}

function formatPercent(value: number) {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

function resizeChart() {
  chartInstance?.resize();
}

function disposeChart() {
  chartInstance?.dispose();
  chartInstance = null;
}

function applyExample(text: string) {
  question.value = text;
  void nextTick(() => {
    const field = document.getElementById("question") as HTMLTextAreaElement | null;
    field?.focus();
  });
}

async function copySql() {
  if (!sql.value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(sql.value);
    sqlCopied.value = true;
    window.setTimeout(() => {
      sqlCopied.value = false;
    }, 1600);
  } catch {
    sqlCopied.value = false;
  }
}

function switchView(view: "chat" | "admin") {
  activeView.value = view;
  if (view === "chat") {
    void nextTick(renderChart);
  }
}
</script>

<template>
  <main class="app-shell">
    <section class="workspace">
      <header class="topbar">
        <div class="brand">
          <span class="brand-seal" aria-hidden="true">问</span>
          <div class="brand-text">
            <h1>掌柜问数</h1>
            <p>NL2SQL Data Agent</p>
          </div>
        </div>
        <div class="topbar-actions">
          <div class="datasource-select">
            <label for="datasource">数据源</label>
            <select
              id="datasource"
              v-model="selectedDatasourceName"
              :disabled="isSubmitting || isLoadingDatasources"
            >
              <option v-for="source in dataSources" :key="source.name" :value="source.name">
                {{ source.display_name }}
              </option>
            </select>
            <button
              type="button"
              class="datasource-refresh"
              :disabled="isSubmitting || isLoadingDatasources"
              @click="fetchDatasources"
            >
              刷新
            </button>
            <span class="datasource-status" :class="{ error: datasourceLoadError }">
              {{ datasourceStatusLabel }}
            </span>
          </div>
          <nav class="view-toggle" aria-label="view switcher">
            <button
              type="button"
              :class="{ active: activeView === 'chat' }"
              @click="switchView('chat')"
            >
              问数
            </button>
            <button
              type="button"
              :class="{ active: activeView === 'admin' }"
              @click="switchView('admin')"
            >
              管理
            </button>
          </nav>
          <span class="status-pill">{{ providerStatusLabel }}</span>
        </div>
      </header>

      <section v-if="activeView === 'chat'" class="chat-layout" aria-label="chat workspace">
        <section class="conversation-panel">
          <form class="composer" @submit.prevent="submitQuestion">
            <div class="composer-heading">
              <label for="question">问题</label>
              <button
                type="button"
                class="datasource-refresh"
                :disabled="isSubmitting"
                @click="startNewConversation"
              >
                新对话
              </button>
            </div>
            <div class="composer-row">
              <textarea
                id="question"
                v-model="question"
                rows="2"
                placeholder="输入经营分析问题"
              />
              <button type="submit" :class="{ 'is-loading': isSubmitting }" :disabled="!canSubmit">
                {{ isSubmitting ? "发送中" : "发送" }}
              </button>
            </div>
          </form>

          <div class="result-area">
            <div v-if="!hasActivity" class="empty-state">
              <span class="empty-seal" aria-hidden="true">数</span>
              <h2>问一句，掌柜替你算账</h2>
              <p>用自然语言提问，自动生成 SQL、执行并解释每一步证据来源。</p>
              <div class="empty-examples">
                <span class="empty-examples-label">试试</span>
                <button
                  v-for="example in exampleQuestions"
                  :key="example"
                  type="button"
                  class="example-chip"
                  @click="applyExample(example)"
                >
                  {{ example }}
                </button>
              </div>
            </div>

            <div v-else class="answer-stack">
              <section v-if="datasourceLoadError" class="error-message">
                <h2>数据源状态异常</h2>
                <p>{{ datasourceLoadError }}</p>
              </section>

              <section v-if="errorMessage" class="error-message">
                <h2>{{ errorKind === "blocked" ? "请求被拒绝" : "请求失败" }}</h2>
                <p>{{ errorMessage }}</p>
                <dl v-if="guardResult || errorStep" class="detail-list">
                  <div v-if="errorStep">
                    <dt>步骤</dt>
                    <dd>{{ errorStep }}</dd>
                  </div>
                  <div v-if="guardResult?.stage">
                    <dt>Guard 阶段</dt>
                    <dd>{{ guardResult.stage }}</dd>
                  </div>
                  <div v-if="guardResult?.reason">
                    <dt>原因</dt>
                    <dd>{{ guardResult.reason }}</dd>
                  </div>
                </dl>
                <div v-if="repairHistory.length" class="repair-summary">
                  已尝试修复 {{ repairHistory.length }} 次
                </div>
              </section>

              <section v-if="hasActivity" class="answer-section">
                <h2>步骤流</h2>
                <div class="step-list">
                  <span
                    v-for="step in stepStates"
                    :key="step.id"
                    :class="step.status"
                  >
                    {{ step.label }}
                  </span>
                </div>
              </section>

              <section
                v-if="isSubmitting && !summary && !sql && !errorMessage"
                class="answer-section"
              >
                <div class="skeleton-card" aria-hidden="true">
                  <span class="skeleton-caption">正在生成回答</span>
                  <span class="skeleton-line lg w-60"></span>
                  <span class="skeleton-line w-90"></span>
                  <span class="skeleton-line w-80"></span>
                </div>
              </section>

              <section v-if="sql || summary || rows.length" class="answer-section">
                <h2>查询信息</h2>
                <div class="meta-strip">
                  <span v-if="isFollowUp" class="info-chip source-vector">追问 · {{ followUpLabel }}</span>
                  <div class="meta-item">
                    <span class="meta-k">数据源</span>
                    <span class="meta-v">{{ resultDatasource.display_name }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-k">方言</span>
                    <span class="meta-v">{{ resultDatasource.dialect }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-k">行数</span>
                    <span class="meta-v num">{{ resultRowCount ?? rows.length }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-k">耗时</span>
                    <span class="meta-v num">{{ formattedElapsedMs }}</span>
                  </div>
                </div>
              </section>

              <section v-if="groundingWarnings.length" class="answer-section grounding-warning-section">
                <h2>语义提示</h2>
                <div
                  v-for="(warning, warningIndex) in groundingWarnings"
                  :key="`${warningIndex}-${warning.concept ?? 'semantic'}-${warning.failure_kind ?? 'warning'}`"
                  class="grounding-warning"
                >
                  <strong>{{ warning.concept ?? "语义校验" }}</strong>
                  <span>{{ warning.message ?? warning.explanation ?? "当前结果可能缺少语义支撑。" }}</span>
                </div>
              </section>

              <section v-if="sql" class="answer-section sql-section">
                <div class="sql-card">
                  <header class="sql-card-head">
                    <h2>SQL</h2>
                    <button type="button" class="copy-button" @click="copySql">
                      {{ sqlCopied ? "已复制" : "复制" }}
                    </button>
                  </header>
                  <pre>{{ sql }}</pre>
                </div>
              </section>

              <section v-if="summary" class="answer-section answer-summary">
                <h2>回答</h2>
                <p>{{ summary }}</p>
              </section>

              <section v-if="canRenderChart" class="answer-section chart-section">
                <h2>图表</h2>
                <div ref="chartContainer" class="chart-canvas" />
              </section>

              <section v-if="rows.length" class="answer-section table-section">
                <h2>结果</h2>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th v-for="column in columns" :key="column">{{ column }}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, rowIndex) in rows" :key="rowIndex">
                        <td v-for="(column, columnIndex) in columns" :key="column">
                          {{ row[columnIndex] }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section
                v-if="explainability || retrievalMeta || planHints.length || runtimeStats"
                class="answer-section explain-section"
              >
                <h2>解释信息</h2>
                <dl class="detail-list">
                  <div v-if="planHints.length || runtimeStats">
                    <dt>性能提示</dt>
                    <dd>
                      <span
                        v-for="hint in planHints"
                        :key="hint"
                        class="info-chip"
                      >
                        {{ hint }}
                      </span>
                      <span v-if="runtimeStats?.execution_time_ms !== undefined" class="info-chip">
                        执行耗时 {{ formatDuration(runtimeStats.execution_time_ms) }}
                      </span>
                      <span v-if="runtimeStats?.rows_read !== undefined" class="info-chip">
                        读取行 {{ runtimeStats.rows_read }}
                      </span>
                      <span v-if="runtimeStats?.bytes_read !== undefined" class="info-chip">
                        读取字节 {{ runtimeStats.bytes_read }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="retrievalMeta?.index_status">
                    <dt>检索状态</dt>
                    <dd>
                      <span
                        :class="['info-chip', retrievalMeta.vector_used ? 'source-vector' : 'source-rule']"
                        :title="retrievalModeTitle(retrievalMeta.vector_used)"
                      >
                        {{ retrievalModeLabel(retrievalMeta.vector_used) }}
                      </span>
                      <span
                        class="info-chip"
                        :title="retrievalStatusTitle(retrievalMeta.index_status)"
                      >
                        {{ retrievalStatusLabel(retrievalMeta.index_status) }}
                      </span>
                      <span v-if="retrievalMeta.stale_reason" class="info-chip">
                        {{ retrievalMeta.stale_reason }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="retrievalMeta?.value_hits?.length">
                    <dt>业务取值</dt>
                    <dd>
                      <span
                        v-for="hit in retrievalMeta.value_hits"
                        :key="`${hit.table_name}.${hit.column_name}:${hit.matched_value}`"
                        class="info-chip source-value"
                      >
                        {{ formatValueHit(hit) }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="hasRetrievalSources">
                    <dt>证据来源</dt>
                    <dd class="source-panel">
                      <div class="source-summary">
                        <span
                          v-for="stat in retrievalSourceStats"
                          :key="stat.id"
                          :class="['info-chip', stat.className]"
                          :title="stat.title"
                        >
                          {{ stat.label }} {{ stat.count }}
                        </span>
                      </div>
                      <div class="source-groups">
                        <details
                          v-for="group in retrievalSourceGroups"
                          :key="group.id"
                          class="source-group"
                        >
                          <summary>
                            <span>{{ group.label }}</span>
                            <span
                              class="source-count"
                              :title="`${group.label}类证据命中的资产数`"
                            >
                              {{ group.items.length }}
                            </span>
                          </summary>
                          <div class="source-group-body">
                            <div
                              v-for="item in group.items"
                              :key="item.assetKey"
                              class="source-item"
                            >
                              <div class="source-item-main">
                                <span class="source-kind">{{ item.assetTypeLabel }}</span>
                                <span class="source-asset">{{ item.assetLabel }}</span>
                              </div>
                              <div class="source-item-sources">
                                <span
                                  v-for="source in item.sources"
                                  :key="`${item.assetKey}:${source.raw}`"
                                  :class="['info-chip', source.className]"
                                  :title="source.raw"
                                >
                                  {{ source.label }}
                                </span>
                              </div>
                            </div>
                          </div>
                        </details>
                      </div>
                    </dd>
                  </div>
                  <div v-if="repairHistory.length">
                    <dt>修复记录</dt>
                    <dd class="repair-list">
                      <article
                        v-for="repair in repairHistory"
                        :key="repair.attempt"
                        class="repair-item"
                      >
                        <header>
                          <span class="info-chip">attempt {{ repair.attempt }}</span>
                          <span
                            :class="[
                              'guard-pill',
                              repair.succeeded ? 'passed' : 'blocked',
                            ]"
                          >
                            {{ repair.succeeded ? "fixed" : "failed" }}
                          </span>
                          <span v-if="repair.final_stage" class="info-chip">
                            {{ repair.final_stage }}
                          </span>
                        </header>
                        <p>
                          {{ repair.error_stage }} / {{ repair.error_kind }}:
                          {{ repair.error_reason }}
                        </p>
                        <pre v-if="repair.original_sql">{{ repair.original_sql }}</pre>
                        <pre v-if="repair.repaired_sql">{{ repair.repaired_sql }}</pre>
                      </article>
                    </dd>
                  </div>
                  <div v-if="explainability?.matched_tables?.length">
                    <dt>命中表</dt>
                    <dd>
                      <span
                        v-for="table in explainability?.matched_tables ?? []"
                        :key="table"
                        class="info-chip"
                      >
                        {{ table }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="explainability?.matched_columns?.length">
                    <dt>命中字段</dt>
                    <dd>
                      <span
                        v-for="column in explainability?.matched_columns ?? []"
                        :key="column"
                        class="info-chip"
                      >
                        {{ column }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="explainability?.join_paths?.length">
                    <dt>Join Path</dt>
                    <dd>
                      <span
                        v-for="joinPath in explainability?.join_paths ?? []"
                        :key="formatJoinPath(joinPath)"
                        class="info-chip"
                      >
                        {{ formatJoinPath(joinPath) }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="explainability?.date_interpretation">
                    <dt>时间解释</dt>
                    <dd>
                      <pre>{{ JSON.stringify(explainability.date_interpretation, null, 2) }}</pre>
                    </dd>
                  </div>
                  <div v-if="guardResult">
                    <dt>Guard 结果</dt>
                    <dd>
                      <span :class="['guard-pill', guardResult.allowed ? 'passed' : 'blocked']">
                        {{ guardResult.allowed ? "passed" : "blocked" }}
                      </span>
                      <span
                        v-if="guardResult.stage && guardResult.stage !== 'passed'"
                        class="info-chip"
                      >
                        {{ guardResult.stage }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="guardResult?.warnings?.length">
                    <dt>Guard 提示</dt>
                    <dd>
                      <span
                        v-for="warning in guardResult.warnings"
                        :key="warning"
                        class="info-chip"
                      >
                        {{ warning }}
                      </span>
                    </dd>
                  </div>
                </dl>
              </section>
            </div>
          </div>

        </section>
      </section>
      <Admin
        v-else
        :data-sources="dataSources"
        :default-datasource="selectedDatasourceName"
      />
    </section>
  </main>
</template>
