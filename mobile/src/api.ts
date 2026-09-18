import Constants from "expo-constants";

export type DriverProfile = {
  id: string;
  name: string;
  email: string;
  phone?: string;
  assignedTruckId?: string;
  truck?: {
    registrationNumber: string;
    status: string;
    refrigerated: boolean;
  } | null;
};

export type Shipment = {
  id: string;
  cargoType: string;
  status: string;
  requiresRefrigeration: boolean;
  requiredMaxTempC?: number | null;
  volumeM3: number;
  weightKg: number;
  valueInr: number;
};

export type ActiveIncident = {
  id: string;
  state: string;
  cargoType: string;
  minutesUntilSpoilage?: number | null;
} | null;

const configuredBaseUrl = Constants.expoConfig?.extra?.apiBaseUrl as string | undefined;
let activeBaseUrl = (configuredBaseUrl || "http://10.0.2.2:8000").replace(/\/$/, "");

export function setApiBaseUrl(url: string) {
  activeBaseUrl = url.replace(/\/$/, "");
}

export function getApiBaseUrl(): string {
  return activeBaseUrl;
}

export class ApiError extends Error {}

export async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`${activeBaseUrl}${path}`, { ...options, headers });
  } catch (error) {
    throw new ApiError(`CargoResQ is offline (${activeBaseUrl}). Check API endpoint and connection.`);
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.detail || "The request could not be completed.");
  return data as T;
}

export async function login(email: string, password: string) {
  const body = new URLSearchParams({ username: email, password });
  return request<{ access_token: string; driver_name: string }>("/api/v1/driver/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString()
  });
}

export function getDriver(token: string) {
  return request<DriverProfile>("/api/v1/driver/me", {}, token);
}

export function getShipment(token: string) {
  return request<{ shipment: Shipment | null }>("/api/v1/driver/active-shipment", {}, token);
}

export function getIncident(token: string) {
  return request<{ incident: ActiveIncident }>("/api/v1/driver/active-incident", {}, token);
}

export function reportBreakdown(token: string, payload: { shipment_id: string; lat: number; lng: number; hours_to_spoilage?: number }) {
  return request<{ incidentId?: string; shipmentId: string; status: string }>("/api/v1/driver/report-breakdown", {
    method: "POST",
    body: JSON.stringify({ ...payload, client_request_id: `android-${Date.now()}` })
  }, token);
}
