<script setup lang="ts">
import { LineChart } from "echarts/charts";
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

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const workflowSteps = [
  { id: "intent_guard", label: "意图检查" },
  { id: "retrieve_context", label: "检索上下文" },
  { id: "build_context", label: "构建上下文" },
  { id: "generate_sql", label: "生成 SQL" },
  { id: "sql_guard", label: "SQL Guard" },
  { id: "execute", label: "执行查询" },
  { id: "summarize", label: "生成回答" },
  { id: "recommend_chart", label: "推荐图表" },
] as const;

type WorkflowStepId = (typeof workflowSteps)[number]["id"];
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
type ChartRecommendation = {
  chart_type?: string;
  x_column?: string | null;
  y_columns?: string[];
  reason?: string;
};
type HealthPayload = {
  status?: string;
  llm_provider?: string;
};

const question = ref("查询最近30天每日销售额和订单数");
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
const guardResult = ref<GuardResult | null>(null);
const chartRecommendation = ref<ChartRecommendation | null>(null);
const chartContainer = ref<HTMLDivElement | null>(null);
const activeView = ref<"chat" | "admin">("chat");
const llmProvider = ref("");
const apiTarget = computed(() => `${API_BASE_URL || "same origin"}/api/chat/query`);
const providerStatusLabel = computed(() => {
  if (llmProvider.value === "deepseek") {
    return "DeepSeek Agent Ready";
  }
  if (llmProvider.value === "mock") {
    return "Mock Agent Ready";
  }
  return "Agent Ready";
});
const canSubmit = computed(() => question.value.trim().length > 0 && !isSubmitting.value);
const hasActivity = computed(
  () =>
    isSubmitting.value ||
    stepStates.value.some((step) => step.status !== "pending") ||
    Boolean(summary.value) ||
    Boolean(errorMessage.value),
);
const canRenderLineChart = computed(
  () =>
    chartRecommendation.value?.chart_type === "line" &&
    Boolean(chartRecommendation.value.x_column) &&
    Boolean(chartRecommendation.value.y_columns?.length) &&
    rows.value.length > 0,
);
let chartInstance: echarts.ECharts | null = null;

onMounted(() => {
  window.addEventListener("resize", resizeChart);
  void fetchAgentStatus();
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
  setStepStatus("intent_guard", "running");
  sql.value = "";
  summary.value = "";
  rows.value = [];
  columns.value = [];
  explainability.value = null;
  retrievalMeta.value = null;
  guardResult.value = null;
  chartRecommendation.value = null;
  disposeChart();

  try {
    const response = await fetch(`${API_BASE_URL}/api/chat/query`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question: question.value.trim() }),
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
  return workflowSteps.map((step) => ({
    ...step,
    status: "pending" as StepStatus,
  }));
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
  }
  if (event === "done") {
    stepStates.value = stepStates.value.map((step) => ({ ...step, status: "completed" }));
    sql.value = payload.sql ?? "";
    summary.value = payload.summary ?? "";
    columns.value = payload.result?.columns ?? [];
    rows.value = payload.result?.rows ?? [];
    chartRecommendation.value = payload.chart_recommendation ?? null;
    explainability.value = payload.explainability ?? null;
    guardResult.value = payload.explainability?.guard_result ?? guardResult.value;
    void nextTick(renderLineChart);
  }
  if (event === "error") {
    failStep(payload.step);
    errorStep.value = payload.step ?? "";
    errorKind.value = payload.error_kind === "blocked" ? "blocked" : "failure";
    errorMessage.value = payload.reason ?? "请求失败";
    explainability.value = payload.explainability ?? null;
    guardResult.value = payload.explainability?.guard_result ?? guardResult.value;
    chartRecommendation.value = null;
    disposeChart();
  }
}

function renderLineChart() {
  if (!canRenderLineChart.value || !chartContainer.value || !chartRecommendation.value) {
    disposeChart();
    return;
  }

  const xColumn = chartRecommendation.value.x_column;
  const yColumns = chartRecommendation.value.y_columns ?? [];
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

  chartInstance ??= echarts.init(chartContainer.value);
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
    yAxis: {
      type: "value",
    },
    series: yIndexes.map(({ column, index }) => ({
      name: column,
      type: "line",
      smooth: true,
      symbolSize: 5,
      data: rows.value.map((row) => Number(row[index] ?? 0)),
    })),
  });
  resizeChart();
}

function resizeChart() {
  chartInstance?.resize();
}

function disposeChart() {
  chartInstance?.dispose();
  chartInstance = null;
}

function switchView(view: "chat" | "admin") {
  activeView.value = view;
  if (view === "chat") {
    void nextTick(renderLineChart);
  }
}
</script>

<template>
  <main class="app-shell">
    <section class="workspace">
      <header class="topbar">
        <div>
          <h1>掌柜问数</h1>
          <p>NL2SQL Data Agent</p>
        </div>
        <div class="topbar-actions">
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
        <aside class="steps-panel">
          <h2>执行步骤</h2>
          <ol>
            <li
              v-for="step in stepStates"
              :key="step.id"
              :class="['workflow-step', step.status]"
            >
              <span>{{ step.label }}</span>
              <strong>{{ step.status }}</strong>
            </li>
          </ol>
        </aside>

        <section class="conversation-panel">
          <div class="result-area">
            <div v-if="!hasActivity" class="empty-state">
              <h2>开始一次问数</h2>
              <p>输入经营分析问题，Agent 会生成 SQL 并返回查询结果。</p>
            </div>

            <div v-else class="answer-stack">
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

              <section v-if="sql" class="answer-section">
                <h2>SQL</h2>
                <pre>{{ sql }}</pre>
              </section>

              <section v-if="summary" class="answer-section">
                <h2>回答</h2>
                <p>{{ summary }}</p>
              </section>

              <section v-if="canRenderLineChart" class="answer-section chart-section">
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

              <section v-if="explainability || retrievalMeta" class="answer-section explain-section">
                <h2>解释信息</h2>
                <dl class="detail-list">
                  <div v-if="retrievalMeta?.index_status">
                    <dt>召回状态</dt>
                    <dd>
                      <span
                        :class="['info-chip', retrievalMeta.vector_used ? 'source-vector' : 'source-rule']"
                      >
                        {{ retrievalMeta.vector_used ? "vector" : "rule-only" }}
                      </span>
                      <span class="info-chip">{{ retrievalMeta.index_status }}</span>
                      <span v-if="retrievalMeta.stale_reason" class="info-chip">
                        {{ retrievalMeta.stale_reason }}
                      </span>
                    </dd>
                  </div>
                  <div v-if="retrievalMeta?.value_hits?.length">
                    <dt>Value Recall</dt>
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
                  <div
                    v-if="
                      retrievalMeta?.retrieval_sources &&
                      Object.keys(retrievalMeta.retrieval_sources).length
                    "
                  >
                    <dt>召回来源</dt>
                    <dd class="source-list">
                      <div
                        v-for="(sources, assetKey) in retrievalMeta.retrieval_sources"
                        :key="assetKey"
                        class="source-row"
                      >
                        <span class="source-asset">{{ assetKey }}</span>
                        <span
                          v-for="source in sources"
                          :key="`${assetKey}:${source}`"
                          :class="['info-chip', retrievalSourceClass(source)]"
                        >
                          {{ source }}
                        </span>
                      </div>
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

          <form class="composer" @submit.prevent="submitQuestion">
            <label for="question">问题</label>
            <div class="composer-row">
              <textarea
                id="question"
                v-model="question"
                rows="3"
                placeholder="输入经营分析问题"
              />
              <button type="submit" :disabled="!canSubmit">
                {{ isSubmitting ? "发送中" : "发送" }}
              </button>
            </div>
            <p class="api-hint">POST {{ apiTarget }}</p>
          </form>
        </section>
      </section>
      <Admin v-else />
    </section>
  </main>
</template>
