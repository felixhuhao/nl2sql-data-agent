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

export async function listDatasources() {
  const response = await fetch(`${API_BASE_URL}/api/datasources`);
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return (await response.json()) as DatasourcesPayload;
}
