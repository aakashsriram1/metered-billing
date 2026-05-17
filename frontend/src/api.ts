const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE_URL = import.meta.env.DEV ? "/api" : configuredBaseUrl || "http://localhost:8000";

type RequestOptions = {
  method?: string;
  body?: unknown;
};

async function requestJson<T>(path: string, headers: Record<string, string>, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const text = await response.text();
      if (text) {
        try {
          const body = JSON.parse(text);
          message = body.detail || JSON.stringify(body);
        } catch {
          message = text;
        }
      }
    } catch {
      // Keep the status fallback if the browser cannot expose the body.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export async function fetchJson<T>(path: string, apiKey: string): Promise<T> {
  return requestJson<T>(path, { Authorization: `Bearer ${apiKey}` });
}

export async function opsFetchJson<T>(path: string, opsToken: string, options: RequestOptions = {}): Promise<T> {
  return requestJson<T>(path, { "X-Ops-Token": opsToken }, options);
}
