<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  createAlias,
  createMetric,
  createVerifiedQuery,
  deleteAlias,
  getAnalysisSpace,
  getVectorIndexStatus,
  listAliases,
  listColumns,
  listMetrics,
  listRelationships,
  listTables,
  listVerifiedQueries,
  rebuildVectorIndex,
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
  type VectorIndexStatus,
  type VerifiedQuery,
} from "./api/admin";
import type { DatasourceInfo } from "./api/datasources";

type AdminTab = "tables" | "metrics" | "aliases" | "verified" | "space" | "relationships" | "vector";
type Editor = "metric" | "alias" | "verified" | "relationship" | null;

const { t } = useI18n();
const tabs = computed<{ id: AdminTab; label: string }[]>(() => [
  { id: "tables", label: t("admin.tabs.tables") },
  { id: "metrics", label: t("admin.tabs.metrics") },
  { id: "aliases", label: t("admin.tabs.aliases") },
  { id: "verified", label: t("admin.tabs.verified") },
  { id: "space", label: t("admin.tabs.space") },
  { id: "relationships", label: t("admin.tabs.relationships") },
  { id: "vector", label: t("admin.tabs.vector") },
]);

const props = defineProps<{
  dataSources: DatasourceInfo[];
  defaultDatasource: string;
}>();

const activeTab = ref<AdminTab>("metrics");
const editor = ref<Editor>(null);
const isLoading = ref(false);
const isSaving = ref(false);
const isRebuilding = ref(false);
const errorMessage = ref("");
const noticeMessage = ref("");
const tables = ref<MetadataTable[]>([]);
const columnsByTable = ref<Record<string, MetadataColumn[]>>({});
const metrics = ref<Metric[]>([]);
const aliases = ref<Alias[]>([]);
const verifiedQueries = ref<VerifiedQuery[]>([]);
const analysisSpace = ref<AnalysisSpace | null>(null);
const relationships = ref<Relationship[]>([]);
const vectorStatus = ref<VectorIndexStatus | null>(null);
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

const activeDatasource = computed(() => props.defaultDatasource || props.dataSources[0]?.name || "duckdb_ecommerce");
const activeDatasourceInfo = computed(
  () => props.dataSources.find((source) => source.name === activeDatasource.value) ?? props.dataSources[0],
);
const selectedTableColumns = computed(() => columnsByTable.value[aliasForm.value.table_name] ?? []);
const editorTitle = computed(() => {
  if (editor.value === "metric") {
    return editingMetricName.value ? t("admin.editMetric") : t("admin.addMetric");
  }
  if (editor.value === "alias") {
    return t("admin.addAlias");
  }
  if (editor.value === "verified") {
    return editingVerifiedId.value ? t("admin.editVerified") : t("admin.addVerified");
  }
  if (editor.value === "relationship") {
    return t("admin.editRelationship");
  }
  return "";
});

watch(activeDatasource, () => {
  closeEditor();
  void loadAdminData();
}, { immediate: true });

async function loadAdminData() {
  isLoading.value = true;
  errorMessage.value = "";
  try {
    const datasource = activeDatasource.value;
    const [tableRows, metricRows, aliasRows, queryRows, space, relationshipRows, indexStatus] = await Promise.all([
      listTables(datasource),
      listMetrics(datasource),
      listAliases(undefined, datasource),
      listVerifiedQueries(datasource),
      getAnalysisSpace(datasource),
      listRelationships(datasource),
      getVectorIndexStatus(),
    ]);
    tables.value = tableRows;
    metrics.value = metricRows;
    aliases.value = aliasRows;
    verifiedQueries.value = queryRows;
    analysisSpace.value = space;
    relationships.value = relationshipRows;
    vectorStatus.value = indexStatus;
    analysisForm.value = {
      tables: [...(space.tables ?? [])],
      enabled_metrics: [...(space.enabled_metrics ?? [])],
      allowed_operations: space.allowed_operations?.length ? [...space.allowed_operations] : ["select"],
    };
    await loadTableColumns(tableRows, datasource);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("admin.loadFailed");
  } finally {
    isLoading.value = false;
  }
}

async function loadTableColumns(tableRows: MetadataTable[], datasource: string) {
  const entries = await Promise.all(
    tableRows.map(async (table) => [table.table_name, await listColumns(table.table_name, datasource)] as const),
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
      await updateMetric(editingMetricName.value, payload, activeDatasource.value);
    } else {
      await createMetric(payload, activeDatasource.value);
    }
  });
}

async function saveAlias() {
  await saveAndRefresh(async () => {
    await createAlias(
      {
        table_name: aliasForm.value.table_name,
        column_name: aliasForm.value.column_name,
        alias: aliasForm.value.alias.trim(),
      },
      activeDatasource.value,
    );
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
      await updateVerifiedQuery(editingVerifiedId.value, payload, activeDatasource.value);
    } else {
      await createVerifiedQuery(
        {
          ...payload,
          query_id: verifiedForm.value.query_id.trim(),
          verified_by: verifiedForm.value.verified_by.trim() || "user",
        },
        activeDatasource.value,
      );
    }
  });
}

async function saveRelationship() {
  if (editingRelationshipId.value === null) {
    return;
  }
  await saveAndRefresh(async () => {
    await updateRelationship(
      editingRelationshipId.value as number,
      {
        confidence: Number(relationshipForm.value.confidence),
        fanout_risk: relationshipForm.value.fanout_risk,
        source: relationshipForm.value.source,
        description: optionalText(relationshipForm.value.description),
      },
      activeDatasource.value,
    );
  });
}

async function saveAnalysisSpace() {
  await saveAndRefresh(async () => {
    await updateAnalysisSpace(
      {
        tables: analysisForm.value.tables,
        enabled_metrics: analysisForm.value.enabled_metrics,
        allowed_operations: ["select"],
      },
      activeDatasource.value,
    );
  }, false);
}

async function saveAndRefresh(action: () => Promise<void>, closePanel = true) {
  isSaving.value = true;
  errorMessage.value = "";
  noticeMessage.value = "";
  try {
    await action();
    noticeMessage.value = t("admin.saved");
    if (closePanel) {
      closeEditor();
    }
    await loadAdminData();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("admin.saveFailed");
  } finally {
    isSaving.value = false;
  }
}

async function toggleMetricEnabled(metric: Metric) {
  await saveAndRefresh(async () => {
    await toggleMetric(metric.name, activeDatasource.value);
  }, false);
}

async function toggleVerifiedEnabled(query: VerifiedQuery) {
  await saveAndRefresh(async () => {
    await toggleVerifiedQuery(query.id, activeDatasource.value);
  }, false);
}

async function removeAlias(alias: Alias) {
  if (!window.confirm(t("admin.deleteAlias", { alias: alias.alias }))) {
    return;
  }
  await saveAndRefresh(async () => {
    await deleteAlias(alias.id, activeDatasource.value);
  }, false);
}

async function rebuildIndex() {
  isRebuilding.value = true;
  errorMessage.value = "";
  noticeMessage.value = "";
  try {
    await rebuildVectorIndex();
    vectorStatus.value = await getVectorIndexStatus();
    noticeMessage.value = t("admin.rebuilt");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("admin.rebuildFailed");
  } finally {
    isRebuilding.value = false;
  }
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

function formatAssetCounts(counts: Record<string, number> | undefined) {
  if (!counts || !Object.keys(counts).length) {
    return "-";
  }
  return Object.entries(counts)
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ");
}

function formatTableOlap(table: MetadataTable) {
  return [
    table.engine ? `engine ${table.engine}` : "",
    table.partition_key ? `partition ${table.partition_key}` : "",
    table.sorting_key ? `sort ${table.sorting_key}` : "",
  ].filter(Boolean);
}

function formatColumnFlags(column: MetadataColumn) {
  return [
    column.nullable ? "nullable" : "",
    column.is_dimension ? "dimension" : "",
    column.is_metric ? "metric" : "",
    column.is_partition_key ? "partition" : "",
    column.is_sorting_key ? "sort" : "",
    column.is_primary_key ? "primary" : "",
    column.low_cardinality ? "low cardinality" : "",
  ].filter(Boolean);
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
      <div class="admin-header-actions">
        <span class="status-pill">
          {{ activeDatasourceInfo?.display_name ?? activeDatasource }}
          <span v-if="activeDatasourceInfo?.dialect">/ {{ activeDatasourceInfo.dialect }}</span>
        </span>
        <button type="button" class="secondary-button" :disabled="isLoading" @click="loadAdminData">
          {{ t("admin.refresh") }}
        </button>
      </div>
    </header>

    <div v-if="errorMessage" class="admin-alert error">{{ errorMessage }}</div>
    <div v-if="noticeMessage && !errorMessage" class="admin-alert success">{{ noticeMessage }}</div>

    <section v-if="activeTab === 'tables'" class="admin-section">
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("admin.table") }}</th>
              <th>{{ t("admin.rows") }}</th>
              <th>OLAP</th>
              <th>{{ t("admin.columns") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="table in tables" :key="table.table_name">
              <td>
                <div class="table-name-cell">
                  <strong>{{ table.table_name }}</strong>
                  <span v-if="table.display_name">{{ table.display_name }}</span>
                  <span v-if="table.description">{{ table.description }}</span>
                </div>
              </td>
              <td>{{ table.row_count ?? 0 }}</td>
              <td>
                <div class="chip-list">
                  <span v-for="part in formatTableOlap(table)" :key="part" class="info-chip">
                    {{ part }}
                  </span>
                  <span v-if="!formatTableOlap(table).length" class="info-chip">-</span>
                </div>
              </td>
              <td>
                <div class="column-list">
                  <div
                    v-for="column in columnsByTable[table.table_name] ?? []"
                    :key="column.column_name"
                    class="column-item"
                  >
                    <span class="code-cell">{{ column.column_name }}</span>
                    <span class="source-kind">{{ column.data_type }}</span>
                    <span
                      v-for="flag in formatColumnFlags(column)"
                      :key="`${column.column_name}:${flag}`"
                      class="info-chip"
                    >
                      {{ flag }}
                    </span>
                  </div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'metrics'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openMetricEditor()">{{ t("admin.addMetric") }}</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("admin.name") }}</th>
              <th>{{ t("admin.label") }}</th>
              <th>{{ t("admin.expression") }}</th>
              <th>{{ t("admin.timeColumn") }}</th>
              <th>{{ t("admin.status") }}</th>
              <th>{{ t("admin.operation") }}</th>
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
                  {{ metric.enabled ? t("admin.enabled") : t("admin.disabled") }}
                </label>
              </td>
              <td>
                <button type="button" class="link-button" @click="openMetricEditor(metric)">{{ t("admin.edit") }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'aliases'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openAliasEditor">{{ t("admin.addAlias") }}</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("admin.table") }}</th>
              <th>{{ t("admin.columns") }}</th>
              <th>{{ t("admin.tabs.aliases") }}</th>
              <th>{{ t("admin.operation") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="alias in aliases" :key="alias.id">
              <td>{{ alias.table_name }}</td>
              <td>{{ alias.column_name }}</td>
              <td>{{ alias.alias }}</td>
              <td>
                <button type="button" class="danger-link" @click="removeAlias(alias)">{{ t("admin.delete") }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'verified'" class="admin-section">
      <div class="admin-actions">
        <button type="button" @click="openVerifiedEditor()">{{ t("admin.addVerified") }}</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>{{ t("admin.question") }}</th>
              <th>SQL</th>
              <th>{{ t("admin.tags") }}</th>
              <th>{{ t("admin.status") }}</th>
              <th>{{ t("admin.operation") }}</th>
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
                  {{ query.enabled ? t("admin.enabled") : t("admin.disabled") }}
                </label>
              </td>
              <td>
                <button type="button" class="link-button" @click="openVerifiedEditor(query)">{{ t("admin.edit") }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'space'" class="admin-section">
      <div class="space-grid">
        <section>
          <h2>{{ t("admin.availableTables") }}</h2>
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
          <h2>{{ t("admin.availableMetrics") }}</h2>
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
          <h2>{{ t("admin.allowedOperations") }}</h2>
          <label class="check-row">
            <input type="checkbox" checked disabled />
            <span>select</span>
          </label>
        </section>
      </div>
      <div class="admin-actions">
        <button type="button" :disabled="isSaving" @click="saveAnalysisSpace">{{ t("admin.saveSpace") }}</button>
      </div>
    </section>

    <section v-if="activeTab === 'relationships'" class="admin-section">
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("admin.relationship") }}</th>
              <th>{{ t("admin.type") }}</th>
              <th>{{ t("admin.source") }}</th>
              <th>{{ t("admin.confidence") }}</th>
              <th>{{ t("admin.fanoutRisk") }}</th>
              <th>{{ t("admin.operation") }}</th>
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
                  {{ t("admin.edit") }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'vector'" class="admin-section">
      <div class="vector-status">
        <dl class="detail-list">
          <div>
            <dt>{{ t("admin.status") }}</dt>
            <dd>
              <span :class="['info-chip', vectorStatus?.status === 'ready' ? 'source-vector' : '']">
                {{ vectorStatus?.status ?? "-" }}
              </span>
              <span v-if="vectorStatus?.vector_mode" class="info-chip">
                {{ vectorStatus.vector_mode }}
              </span>
            </dd>
          </div>
          <div>
            <dt>{{ t("admin.model") }}</dt>
            <dd>{{ vectorStatus?.embedding_model || "-" }}</dd>
          </div>
          <div>
            <dt>{{ t("admin.dimension") }}</dt>
            <dd>{{ vectorStatus?.embedding_dimension ?? "-" }}</dd>
          </div>
          <div>
            <dt>{{ t("admin.builtAt") }}</dt>
            <dd>{{ vectorStatus?.built_at || "-" }}</dd>
          </div>
          <div>
            <dt>{{ t("admin.assetCount") }}</dt>
            <dd>{{ formatAssetCounts(vectorStatus?.asset_counts) }}</dd>
          </div>
          <div>
            <dt>Qdrant</dt>
            <dd>
              {{ vectorStatus?.qdrant_url || "-" }}
              <span v-if="vectorStatus?.qdrant_collection_prefix">
                / {{ vectorStatus.qdrant_collection_prefix }}
              </span>
            </dd>
          </div>
          <div v-if="vectorStatus?.stale_reason">
            <dt>{{ t("admin.hint") }}</dt>
            <dd>{{ vectorStatus.stale_reason }}</dd>
          </div>
        </dl>
      </div>
      <div class="admin-actions">
        <button
          type="button"
          :disabled="isRebuilding || !vectorStatus?.vector_enabled"
          @click="rebuildIndex"
        >
          {{ isRebuilding ? t("admin.rebuilding") : t("admin.rebuildIndex") }}
        </button>
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
            {{ t("admin.name") }}
            <input v-model="metricForm.name" :disabled="Boolean(editingMetricName)" required />
          </label>
          <label>
            {{ t("admin.label") }}
            <input v-model="metricForm.label" required />
          </label>
          <label>
            {{ t("admin.expression") }}
            <textarea v-model="metricForm.expression" rows="4" required />
          </label>
          <label>
            {{ t("admin.description") }}
            <textarea v-model="metricForm.description" rows="3" />
          </label>
          <label>
            {{ t("admin.timeColumn") }}
            <input v-model="metricForm.default_time_column" placeholder="dim_date.date_value" />
          </label>
          <label>
            {{ t("admin.dimension") }}
            <input v-model="metricForm.allowed_dimensions" placeholder="date, channel, region" />
          </label>
        </template>

        <template v-if="editor === 'alias'">
          <label>
            {{ t("admin.table") }}
            <select v-model="aliasForm.table_name" @change="aliasForm.column_name = ''">
              <option v-for="table in tables" :key="table.table_name" :value="table.table_name">
                {{ table.table_name }}
              </option>
            </select>
          </label>
          <label>
            {{ t("admin.columns") }}
            <select v-model="aliasForm.column_name" required>
              <option value="" disabled>{{ t("admin.chooseColumn") }}</option>
              <option v-for="column in selectedTableColumns" :key="column.column_name" :value="column.column_name">
                {{ column.column_name }}
              </option>
            </select>
          </label>
          <label>
            {{ t("admin.tabs.aliases") }}
            <input v-model="aliasForm.alias" required />
          </label>
        </template>

        <template v-if="editor === 'verified'">
          <label>
            ID
            <input v-model="verifiedForm.query_id" :disabled="Boolean(editingVerifiedId)" required />
          </label>
          <label>
            {{ t("admin.question") }}
            <input v-model="verifiedForm.question" required />
          </label>
          <label>
            SQL
            <textarea v-model="verifiedForm.sql" rows="7" required />
          </label>
          <label>
            {{ t("admin.tags") }}
            <input v-model="verifiedForm.tags" placeholder="sales, demo" />
          </label>
          <label>
            {{ t("admin.validator") }}
            <input v-model="verifiedForm.verified_by" :disabled="Boolean(editingVerifiedId)" />
          </label>
        </template>

        <template v-if="editor === 'relationship'">
          <label>
            {{ t("admin.confidence") }}
            <input v-model.number="relationshipForm.confidence" type="number" min="0" max="1" step="0.01" />
          </label>
          <label>
            {{ t("admin.fanoutRisk") }}
            <select v-model="relationshipForm.fanout_risk">
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <label>
            {{ t("admin.source") }}
            <select v-model="relationshipForm.source">
              <option value="inferred">inferred</option>
              <option value="overlay">overlay</option>
            </select>
          </label>
          <label>
            {{ t("admin.description") }}
            <textarea v-model="relationshipForm.description" rows="4" />
          </label>
        </template>

        <footer>
          <button type="submit" :disabled="isSaving">{{ t("admin.save") }}</button>
          <button type="button" class="secondary-button" @click="closeEditor">{{ t("admin.cancel") }}</button>
        </footer>
      </form>
    </aside>
  </section>
</template>
