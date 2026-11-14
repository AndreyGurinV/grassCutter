export type CartStatus = {
  armed: boolean;
  mock: boolean;
  speeds: number[];
  reverse: boolean[];
  dac_codes: number[];
  dac_volts: number[];
  estop_en: boolean;
};

export type ApiResponse = {
  ok: boolean;
  command?: string;
  status?: CartStatus;
  stdout?: string;
  stderr?: string;
  error?: string;
};

async function request(path: string, init?: RequestInit): Promise<ApiResponse> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const data = (await res.json()) as ApiResponse;
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

export const api = {
  health: () => fetch("/health").then((r) => r.json() as Promise<{ status: string }>),
  status: () => request("/status"),
  arm: () => request("/arm", { method: "POST" }),
  disarm: () => request("/disarm", { method: "POST" }),
  stop: () => request("/stop", { method: "POST" }),
  drive: (v: number, w: number) =>
    request("/drive", { method: "POST", body: JSON.stringify({ v, w }) }),
  wheel: (index: number, speed: number) =>
    request("/wheel", { method: "POST", body: JSON.stringify({ index, speed }) }),
};
