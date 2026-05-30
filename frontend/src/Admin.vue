<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  createAlias,
  createMetric,
  createVerifiedQuery,
  deleteAlias,
  getAnalysisSpace,
  listAliases,
  listColumns,
  listMetrics,
  listRelationships,
  listTables,
  listVerifiedQueries,
  toggleMetric,
  toggleVerifiedQuery,
  updateAnalysisSpace,
  updateMetric,
  updateRelationship,
  updateVerifiedQuery,
  type Alias,
  type AnalysisSpace,
  type MetadataColumn,
  type MetadataTable,
  type Metric,
  type Relationship,
  type VerifiedQuery,
} from "./api/admin";

type AdminTab = "metrics" | "aliases" | "verified" | "space" | "relationships";
type Editor = "metric" | "alias" | "verified" | "relationship" | null;

const tabs: { id: AdminTab; label: string }[] = [
  { id: "metrics", label: "指标" },
  { id: "aliases", label: "别名" },
  { id: "verified", label: "验证查询" },
  { id: "space", label: "分析空间" },
  { id: "relationships", label: "关系" },
];

const activeTab = ref<AdminTab>("metrics");
const editor = ref<Editor>(null);
const isLoading = ref(false);
const isSaving = ref(false);
const errorMessage = ref("");
const noticeMessage = ref("");
const tables = ref<MetadataTable[]>([]);
const columnsByTable = ref<Record<string, MetadataColumn[]>>({});
const metrics = ref<Metric[]>([]);
const aliases = ref<Alias[]>([]);
const verifiedQueries = ref<VerifiedQuery[]>([]);
const analysisSpace = ref<AnalysisSpace | null>(null);
const relationships = ref<Relationship[]>([]);
const editingMetricName = ref("");
const editingVerifiedId = ref("");
const editingRelationshipId = ref<number | null>(null);

const metricForm = ref({
  name: "",
  label: "",
  expression: "",
  description: "",
  default_time_column: "",
  allowed_dimensions: "",
});
const aliasForm = ref({ table_name: "", column_name: "", alias: "" });
const verifiedForm = ref({
  query_id: "",
  question: "",
  sql: "",
  tags: "",
  verified_by: "user",
});
const analysisForm = ref({
  tables: [] as string[],
  enabled_metrics: [] as string[],
  allowed_operations: ["select"] as string[],
});
const relationshipForm = ref({
  confidence: 1,
  fanout_risk: "low",
  source: "overlay",
  description: "",
});

const selectedTableColumns = computed(() => columnsByTable.value[aliasForm.value.table_name] ?? []);
const editorTitle = computed(() => {
  if (editor.value === "metric") {
    return editingMetricName.value ? "编辑指标" : "新增指标";
  }
  if (editor.value === "alias") {
    return "新增别名";
  }
  if (editor.value === "verified") {
    return editingVerifiedId.value ? "编辑验证查询" : "新增验证查询";
  }
  if (editor.value === "relationship") {
    return "编辑关系";
  }
  return "";
});

onMounted(loadAdminData);

async function loadAdminData() {
  isLoading.value = true;
  errorMessage.value = "";
  try {
    const [tableRows, metricRows, aliasRows, queryRows, space, relationshipRows] = await Promise.all([
      listTables(),
      listMetrics(),
      listAliases(),
      listVerifiedQueries(),
      getAnalysisSpace(),
      listRelationships(),
    ]);
    tables.value = tableRows;
    metrics.value = metricRows;
    aliases.value = aliasRows;
    verifiedQueries.value = queryRows;
    analysisSpace.value = space;
    relationships.value = relationshipRows;
    analysisForm.value = {
      tables: [...(space.tables ?? [])],
      enabled_metrics: [...(space.enabled_metrics ?? [])],
      allowed_operations: space.allowed_operations?.length ? [...space.allowed_operations] : ["select"],
    };
    await loadTableColumns(tableRows);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "加载失败";
  } finally {
    isLoading.value = false;
  }
}

async function loadTableColumns(tableRows: MetadataTable[]) {
  const entries = await Promise.all(
    tableRows.map(async (table) => [table.table_name, await listColumns(table.table_name)] as const),
  );
  columnsByTable.value = Object.fromEntries(entries);
}

function openMetricEditor(metric?: Metric) {
  editor.value = "metric";
  editingMetricName.value = metric?.name ?? "";
  metricForm.value = {
    name: metric?.name ?? "",
    label: metric?.label ?? "",
    expression: metric?.expression ?? "",
    description: metric?.description ?? "",
    default_time_column: metric?.default_time_column ?? "",
    allowed_dimensions: metric?.allowed_dimensions.join(", ") ?? "",
  };
}

function openAliasEditor() {
  editor.value = "alias";
  aliasForm.value = {
    table_name: tables.value[0]?.table_name ?? "",
    column_name: "",
    alias: "",
  };
}

function openVerifiedEditor(query?: VerifiedQuery) {
  editor.value = "verified";
  editingVerifiedId.value = query?.id ?? "";
  verifiedForm.value = {
    query_id: query?.id ?? "",
    question: query?.question ?? "",
    sql: query?.sql ?? "",
    tags: query?.tags.join(", ") ?? "",
    verified_by: query?.verified_by ?? "user",
  };
}

function openRelationshipEditor(relationship: Relationship) {
  editor.value = "relationship";
  editingRelationshipId.value = relationship.id;
  relationshipForm.value = {
    confidence: relationship.confidence,
    fanout_risk: relationship.fanout_risk,
    source: relationship.source,
    description: relationship.description ?? "",
  };
}

function closeEditor() {
  editor.value = null;
  editingMetricName.value = "";
  editingVerifiedId.value = "";
  editingRelationshipId.value = null;
  errorMessage.value = "";
}

async function submitEditor() {
  if (editor.value === "metric") {
    await saveMetric();
  } else if (editor.value === "alias") {
    await saveAlias();
  } else if (editor.value === "verified") {
    await saveVerifiedQuery();
  } else if (editor.value === "relationship") {
    await saveRelationship();
  }
}

async function saveMetric() {
  await saveAndRefresh(async () => {
    const payload = {
      name: metricForm.value.name.trim(),
      label: metricForm.value.label.trim(),
      expression: metricForm.value.expression.trim(),
      description: optionalText(metricForm.value.description),
      default_time_column: optionalText(metricForm.value.default_time_column),
      allowed_dimensions: splitList(metricForm.value.allowed_dimensions),
    };
    if (editingMetricName.value) {
      await updateMetric(editingMetricName.value, payload);
    } else {
      await createMetric(payload);
    }
  });
}

async function saveAlias() {
  await saveAndRefresh(async () => {
    await createAlias({
      table_name: aliasForm.value.table_name,
      column_name: aliasForm.value.column_name,
      alias: aliasForm.value.alias.trim(),
    });
  });
}

async function saveVerifiedQuery() {
  await saveAndRefresh(async () => {
    const payload = {
      question: verifiedForm.value.question.trim(),
      sql: verifiedForm.value.sql.trim(),
      tags: splitList(verifiedForm.value.tags),
    };
    if (editingVerifiedId.value) {
      await updateVerifiedQuery(editingVerifiedId.value, payload);
    } else {
      await createVerifiedQuery({
        ...payload,
        query_id: verifiedForm.value.query_id.trim(),
        verified_by: verifiedForm.value.verified_by.trim() || "user",
      });
    }
  });
}

async function saveRelationship() {
  if (editingRelationshipId.value === null) {
    return;
  }
  await saveAndRefresh(async () => {
    await updateRelationship(editingRelationshipId.value as number, {
      confidence: Number(relationshipForm.value.confidence),
      fanout_risk: relationshipForm.value.fanout_risk,
      source: relationshipForm.value.source,
      description: optionalText(relationshipForm.value.description),
    });
  });
}

async function saveAnalysisSpace() {
  await saveAndRefresh(async () => {
    await updateAnalysisSpace({
      tables: analysisForm.value.tables,
      enabled_metrics: analysisForm.value.enabled_metrics,
      allowed_operations: ["select"],
    });
  }, false);
}

async function saveAndRefresh(action: () => Promise<void>, closePanel = true) {
  isSaving.value = true;
  errorMessage.value = "";
  noticeMessage.value = "";
  try {
    await action();
    noticeMessage.value = "已保存";
    if (closePanel) {
      closeEditor();
    }
    await loadAdminData();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "保存失败";
  } finally {
    isSaving.value = false;
  }
}

async function toggleMetricEnabled(metric: Metric) {
  await saveAndRefresh(async () => {
    await toggleMetric(metric.name);
  }, false);
}

async function toggleVerifiedEnabled(query: VerifiedQuery) {
  await saveAndRefresh(async () => {
    await toggleVerifiedQuery(query.id);
  }, false);
}

async function removeAlias(alias: Alias) {
  if (!window.confirm(`删除别名 ${alias.alias}?`)) {
    return;
  }
  await saveAndRefresh(async () => {
    await deleteAlias(alias.id);
  }, false);
}

function toggleAnalysisTable(tableName: string) {
  toggleArrayItem(analysisForm.value.tables, tableName);
}

function toggleAnalysisMetric(metricName: string) {
  toggleArrayItem(analysisForm.value.enabled_metrics, metricName);
}

function toggleArrayItem(items: string[], value: string) {
  const index = items.indexOf(value);
  if (index >= 0) {
    items.splice(index, 1);
  } else {
    items.push(value);
  }
}

function splitList(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function optionalText(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function formatRelationship(relationship: Relationship) {
  return `${relationship.source_table}.${relationship.source_column} -> ${relationship.target_table}.${relationship.target_column}`;
}
</script>

<template>
  <section class="admin-panel" aria-label="metadata admin">
    <header class="admin-header">
      <div class="admin-tabs" role="tablist">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          :class="{ active: activeTab === tab.id }"
          @click="activeTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>
      <button type="button" class="secondary-button" :disabled="isLoading" @click="loadAdminData">
        刷新
      </button>
    </header>

    <div v-if="errorMessage" class="admin-alert error">{{ errorMessage }}</div>
    <div v-if="noticeMessage && !errorMessage" class="admin-alert success">{{ noticeMessage }}</div>

    <section v-if="activeTab === 'metrics'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openMetricEditor()">新增指标</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>标签</th>
              <th>表达式</th>
              <th>时间列</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="metric in metrics" :key="metric.name">
              <td>{{ metric.name }}</td>
              <td>{{ metric.label }}</td>
              <td class="code-cell">{{ metric.expression }}</td>
              <td>{{ metric.default_time_column || "-" }}</td>
              <td>
                <label class="switch-label">
                  <input type="checkbox" :checked="metric.enabled" @change="toggleMetricEnabled(metric)" />
                  {{ metric.enabled ? "启用" : "停用" }}
                </label>
              </td>
              <td>
                <button type="button" class="link-button" @click="openMetricEditor(metric)">编辑</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'aliases'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openAliasEditor">新增别名</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>表</th>
              <th>列</th>
              <th>别名</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="alias in aliases" :key="alias.id">
              <td>{{ alias.table_name }}</td>
              <td>{{ alias.column_name }}</td>
              <td>{{ alias.alias }}</td>
              <td>
                <button type="button" class="danger-link" @click="removeAlias(alias)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'verified'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openVerifiedEditor()">新增验证查询</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>问题</th>
              <th>SQL</th>
              <th>标签</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="query in verifiedQueries" :key="query.id">
              <td>{{ query.id }}</td>
              <td>{{ query.question }}</td>
              <td class="code-cell">{{ query.sql }}</td>
              <td>{{ query.tags.join(", ") || "-" }}</td>
              <td>
                <label class="switch-label">
                  <input type="checkbox" :checked="query.enabled" @change="toggleVerifiedEnabled(query)" />
                  {{ query.enabled ? "启用" : "停用" }}
                </label>
              </td>
              <td>
                <button type="button" class="link-button" @click="openVerifiedEditor(query)">编辑</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'space'" class="admin-section">
      <div class="space-grid">
        <section>
          <h2>可问表</h2>
          <label v-for="table in tables" :key="table.table_name" class="check-row">
            <input
              type="checkbox"
              :checked="analysisForm.tables.includes(table.table_name)"
              @change="toggleAnalysisTable(table.table_name)"
            />
            <span>{{ table.table_name }}</span>
          </label>
        </section>
        <section>
          <h2>可用指标</h2>
          <label v-for="metric in metrics" :key="metric.name" class="check-row">
            <input
              type="checkbox"
              :checked="analysisForm.enabled_metrics.includes(metric.name)"
              @change="toggleAnalysisMetric(metric.name)"
            />
            <span>{{ metric.label }} ({{ metric.name }})</span>
          </label>
        </section>
        <section>
          <h2>允许操作</h2>
          <label class="check-row">
            <input type="checkbox" checked disabled />
            <span>select</span>
          </label>
        </section>
      </div>
      <div class="admin-actions">
        <button type="button" :disabled="isSaving" @click="saveAnalysisSpace">保存分析空间</button>
      </div>
    </section>

    <section v-if="activeTab === 'relationships'" class="admin-section">
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>关系</th>
              <th>类型</th>
              <th>来源</th>
              <th>置信度</th>
              <th>扇出风险</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="relationship in relationships" :key="relationship.id">
              <td>{{ formatRelationship(relationship) }}</td>
              <td>{{ relationship.relationship_type }}</td>
              <td>{{ relationship.source }}</td>
              <td>{{ relationship.confidence.toFixed(2) }}</td>
              <td>{{ relationship.fanout_risk }}</td>
              <td>
                <button type="button" class="link-button" @click="openRelationshipEditor(relationship)">
                  编辑
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <aside v-if="editor" class="edit-panel" aria-label="edit panel">
      <form class="edit-panel-body" @submit.prevent="submitEditor">
        <header>
          <h2>{{ editorTitle }}</h2>
          <button type="button" class="icon-button" @click="closeEditor">×</button>
        </header>

        <template v-if="editor === 'metric'">
          <label>
            名称
            <input v-model="metricForm.name" :disabled="Boolean(editingMetricName)" required />
          </label>
          <label>
            标签
            <input v-model="metricForm.label" required />
          </label>
          <label>
            表达式
            <textarea v-model="metricForm.expression" rows="4" required />
          </label>
          <label>
            描述
            <textarea v-model="metricForm.description" rows="3" />
          </label>
          <label>
            时间列
            <input v-model="metricForm.default_time_column" placeholder="dim_date.date_value" />
          </label>
          <label>
            维度
            <input v-model="metricForm.allowed_dimensions" placeholder="date, channel, region" />
          </label>
        </template>

        <template v-if="editor === 'alias'">
          <label>
            表
            <select v-model="aliasForm.table_name" @change="aliasForm.column_name = ''">
              <option v-for="table in tables" :key="table.table_name" :value="table.table_name">
                {{ table.table_name }}
              </option>
            </select>
          </label>
          <label>
            列
            <select v-model="aliasForm.column_name" required>
              <option value="" disabled>选择列</option>
              <option v-for="column in selectedTableColumns" :key="column.column_name" :value="column.column_name">
                {{ column.column_name }}
              </option>
            </select>
          </label>
          <label>
            别名
            <input v-model="aliasForm.alias" required />
          </label>
        </template>

        <template v-if="editor === 'verified'">
          <label>
            ID
            <input v-model="verifiedForm.query_id" :disabled="Boolean(editingVerifiedId)" required />
          </label>
          <label>
            问题
            <input v-model="verifiedForm.question" required />
          </label>
          <label>
            SQL
            <textarea v-model="verifiedForm.sql" rows="7" required />
          </label>
          <label>
            标签
            <input v-model="verifiedForm.tags" placeholder="sales, demo" />
          </label>
          <label>
            验证者
            <input v-model="verifiedForm.verified_by" :disabled="Boolean(editingVerifiedId)" />
          </label>
        </template>

        <template v-if="editor === 'relationship'">
          <label>
            置信度
            <input v-model.number="relationshipForm.confidence" type="number" min="0" max="1" step="0.01" />
          </label>
          <label>
            扇出风险
            <select v-model="relationshipForm.fanout_risk">
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <label>
            来源
            <select v-model="relationshipForm.source">
              <option value="inferred">inferred</option>
              <option value="overlay">overlay</option>
            </select>
          </label>
          <label>
            描述
            <textarea v-model="relationshipForm.description" rows="4" />
          </label>
        </template>

        <footer>
          <button type="submit" :disabled="isSaving">保存</button>
          <button type="button" class="secondary-button" @click="closeEditor">取消</button>
        </footer>
      </form>
    </aside>
  </section>
</template>
