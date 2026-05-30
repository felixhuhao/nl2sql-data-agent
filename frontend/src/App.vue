<script setup lang="ts">
import { computed, ref } from "vue";
import { API_BASE_URL } from "./api/config";

const question = ref("查询最近30天每日销售额和订单数");
const isSubmitting = ref(false);
const errorMessage = ref("");
const steps = ref<string[]>([]);
const sql = ref("");
const summary = ref("");
const rows = ref<unknown[][]>([]);
const columns = ref<string[]>([]);
const apiTarget = computed(() => `${API_BASE_URL || "same origin"}/api/chat/query`);
const canSubmit = computed(() => question.value.trim().length > 0 && !isSubmitting.value);

async function submitQuestion() {
  if (!canSubmit.value) {
    return;
  }

  isSubmitting.value = true;
  errorMessage.value = "";
  steps.value = [];
  sql.value = "";
  summary.value = "";
  rows.value = [];
  columns.value = [];

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
    errorMessage.value = error instanceof Error ? error.message : "请求失败";
  } finally {
    isSubmitting.value = false;
  }
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

  const payload = JSON.parse(data);
  if (event === "step") {
    steps.value.push(payload.step);
  }
  if (event === "done") {
    sql.value = payload.sql ?? "";
    summary.value = payload.summary ?? "";
    columns.value = payload.result?.columns ?? [];
    rows.value = payload.result?.rows ?? [];
  }
  if (event === "error") {
    errorMessage.value = payload.reason ?? "请求失败";
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
        <span class="status-pill">Mock Agent Ready</span>
      </header>

      <section class="chat-layout" aria-label="chat workspace">
        <aside class="steps-panel">
          <h2>执行步骤</h2>
          <ol>
            <li>build_context</li>
            <li>generate_sql</li>
            <li>sql_guard</li>
            <li>execute</li>
            <li>summarize</li>
            <li>recommend_chart</li>
          </ol>
        </aside>

        <section class="conversation-panel">
          <div class="result-area">
            <div v-if="!steps.length && !summary && !errorMessage" class="empty-state">
              <h2>开始一次问数</h2>
              <p>输入经营分析问题，Agent 会生成 SQL 并返回查询结果。</p>
            </div>

            <div v-else class="answer-stack">
              <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>

              <section v-if="steps.length" class="answer-section">
                <h2>执行步骤</h2>
                <div class="step-list">
                  <span v-for="step in steps" :key="step">{{ step }}</span>
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
    </section>
  </main>
</template>
