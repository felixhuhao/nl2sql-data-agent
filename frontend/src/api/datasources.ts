import { API_BASE_URL } from "./config";

export type DatasourceInfo = {
  name: string;
  dialect: string;
  display_name: string;
  status: string;
};

export type DatasourcesPayload = {
  sources: DatasourceInfo[];
  default: string | null;
};

export class DatasourceApiError extends Error {
  constructor(readonly status: number) {
    super(`Request failed with status ${status}`);
    this.name = "DatasourceApiError";
  }
}

export async function listDatasources() {
  const response = await fetch(`${API_BASE_URL}/api/datasources`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new DatasourceApiError(response.status);
  }
  return (await response.json()) as DatasourcesPayload;
}
