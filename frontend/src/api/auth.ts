import { API_BASE_URL } from "./config";

export type AuthUser = {
  user_id: string;
  username: string;
  role: string;
};

export class AuthApiError extends Error {
  constructor(readonly status: number) {
    super(`Request failed with status ${status}`);
    this.name = "AuthApiError";
  }
}

export async function getMe() {
  const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
    credentials: "include",
  });
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw new AuthApiError(response.status);
  }
  return (await response.json()) as AuthUser;
}

export async function login(username: string, password: string) {
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (response.status === 401) {
    throw new Error("用户名或密码不正确");
  }
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return (await response.json()) as AuthUser;
}

export async function logout() {
  await fetch(`${API_BASE_URL}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
}
