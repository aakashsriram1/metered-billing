const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE_URL = import.meta.env.DEV ? "/api" : configuredBaseUrl || "http://localhost:8000";

export async function fetchJson<T>(path: string, apiKey: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Authorization: `Bearer ${apiKey}`,
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || JSON.stringify(body);
    } catch {
      const text = await response.text();
      if (text) message = text;
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}
