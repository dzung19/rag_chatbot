export class ApiError extends Error {
  status: number;
  data?: unknown;

  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

let apiKey = "";
let unauthorizedHandler: (() => void) | null = null;

// Base URL for API requests.
// In dev, Vite proxies /api to http://127.0.0.1:8000.
// In prod, Nginx proxies /api to http://gateway:8000.
const BASE_URL = "/api/v1";

export function setApiKey(key: string): void {
  apiKey = key;
}

export function getApiKey(): string {
  return apiKey;
}

export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

export async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const url = `${BASE_URL}${path}`;
  const headers = new Headers(options.headers || {});

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  // Set default JSON Content-Type only if not FormData
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, {
    ...options,
    headers,
    cache: options.cache || "no-store",
  });

  if (!response.ok) {
    let errorMsg = `HTTP ${response.status}`;
    let errorData: unknown;
    try {
      errorData = await response.json();
      if (typeof errorData === "object" && errorData !== null && "detail" in errorData) {
        errorMsg = String((errorData as { detail: unknown }).detail);
      }
    } catch {
      // Non-JSON response
    }

    if (response.status === 401 || response.status === 403) {
      if (unauthorizedHandler) {
        unauthorizedHandler();
      }
    }

    throw new ApiError(errorMsg, response.status, errorData);
  }

  return response;
}

export async function getJSON<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await request(path, { ...options, method: "GET" });
  return (await res.json()) as T;
}

export async function postJSON<T>(path: string, body: unknown, options: RequestInit = {}): Promise<T> {
  const res = await request(path, {
    ...options,
    method: "POST",
    body: JSON.stringify(body),
  });
  return (await res.json()) as T;
}

export async function deleteRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await request(path, { ...options, method: "DELETE" });
  return (await res.json()) as T;
}

export async function uploadFile<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await request(path, {
    method: "POST",
    body: formData,
  });
  return (await res.json()) as T;
}
