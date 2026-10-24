/**
 * Typed API client.
 *
 * Every request goes through `request()`, so error handling is in one place: a
 * non-2xx response becomes an `ApiError` carrying the backend's `detail` message,
 * which the UI surfaces verbatim rather than showing a generic failure.
 */
import type {
  Account,
  AppSettings,
  AuditEntry,
  DefenseComparison,
  DefenseConfigInfo,
  DocumentationPayload,
  Incident,
  IncidentDetail,
  ModelEvaluation,
  ModelInfo,
  MonitoringEvent,
  NetworkGraph,
  OverviewPayload,
  Paginated,
  Replay,
  ScenarioInfo,
  ScoreResponse,
  SimulationRun,
  SimulationRunDetail,
  Transaction,
  TransactionDetail,
} from './types';

const API_BASE = '/api';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit, root = false): Promise<T> {
  const url = root ? path : `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the RupeeShield API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') {
        detail = body.detail;
      } else if (body.detail) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      /* response had no JSON body; keep the status line */
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  });
  const serialised = search.toString();
  return serialised ? `?${serialised}` : '';
}

export interface MonitoringEventPage {
  events: MonitoringEvent[];
  total: number;
  run_id: string | null;
  generated_at: string;
}

export interface AccountEventsPayload {
  account: NetworkGraph['nodes'][number];
  security_events: Record<string, unknown>[];
  transactions: Record<string, unknown>[];
  incidents: Record<string, unknown>[];
  observations: string[];
}

export const api = {
  health: () =>
    request<{ status: string; tracked_accounts: number; models_loaded: boolean }>('/health'),

  overview: () => request<OverviewPayload>('/overview'),

  scenarios: () => request<Paginated<ScenarioInfo>>('/scenarios'),

  defenseConfigs: () => request<Paginated<DefenseConfigInfo>>('/defense/configs'),

  // -- simulation ---------------------------------------------------------
  simulationRuns: (params: { limit?: number; offset?: number; scenario?: string } = {}) =>
    request<Paginated<SimulationRun>>(`/simulation/runs${query(params)}`),

  createSimulationRun: (body: {
    scenario: string;
    n_events: number;
    seed: number;
    defense_config: string;
    mode: string;
    pace_per_second: number;
  }) =>
    request<SimulationRunDetail>('/simulation/runs', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  simulationRun: (runId: string) => request<SimulationRunDetail>(`/simulation/runs/${runId}`),

  stopSimulationRun: (runId: string) =>
    request<SimulationRunDetail>(`/simulation/runs/${runId}/stop`, { method: 'POST' }),

  resetSimulationRun: (runId: string) =>
    request<{ message: string }>(`/simulation/runs/${runId}`, { method: 'DELETE' }),

  // -- monitoring ---------------------------------------------------------
  monitoringEvents: (params: { run_id?: string; since_sequence?: number; limit?: number } = {}) =>
    request<MonitoringEventPage>(`/monitoring/events${query(params)}`),

  /** URL for the SSE endpoint. Events originate from the simulation engine only. */
  monitoringStreamUrl: (runId?: string) =>
    `${API_BASE}/monitoring/stream${query({ run_id: runId ?? undefined })}`,

  // -- ledger -------------------------------------------------------------
  transactions: (
    params: {
      account_id?: string;
      run_id?: string;
      decision?: string;
      status?: string;
      scenario?: string;
      min_amount?: number;
      limit?: number;
      offset?: number;
    } = {},
  ) => request<Paginated<Transaction>>(`/transactions${query(params)}`),

  transaction: (transactionId: string) => request<TransactionDetail>(`/transactions/${transactionId}`),

  accounts: (params: { limit?: number; offset?: number; risk_status?: string } = {}) =>
    request<Paginated<Account>>(`/accounts${query(params)}`),

  account: (accountId: string) => request<Account>(`/accounts/${accountId}`),

  accountEvents: (accountId: string) => request<AccountEventsPayload>(`/accounts/${accountId}/events`),

  // -- network ------------------------------------------------------------
  networkGraph: (params: { run_id?: string; incident_id?: string; limit?: number } = {}) =>
    request<NetworkGraph>(`/network/graph${query(params)}`),

  // -- incidents ----------------------------------------------------------
  incidents: (params: { status?: string; scenario?: string; limit?: number; offset?: number } = {}) =>
    request<Paginated<Incident>>(`/incidents${query(params)}`),

  incident: (incidentId: string) => request<IncidentDetail>(`/incidents/${incidentId}`),

  updateIncidentStatus: (incidentId: string, status: string) =>
    request<IncidentDetail>(`/incidents/${incidentId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),

  addIncidentNote: (incidentId: string, note: string) =>
    request<IncidentDetail>(`/incidents/${incidentId}/notes`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),

  replayIncident: (incidentId: string) =>
    request<Replay>(`/incidents/${incidentId}/replay`, { method: 'POST' }),

  // -- defense ------------------------------------------------------------
  compareDefenses: (body: { scenario: string; n_events: number; seed: number }) =>
    request<DefenseComparison>('/defense/compare', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  defenseComparisons: (params: { limit?: number } = {}) =>
    request<Paginated<DefenseComparison>>(`/defense/comparisons${query(params)}`),

  // -- ml -----------------------------------------------------------------
  modelInfo: () => request<ModelInfo>('/ml/model-info'),

  modelEvaluation: () => request<ModelEvaluation>('/ml/evaluation'),

  featureImportance: () =>
    request<{ items: { feature: string; importance: number }[]; total: number }>(
      '/ml/feature-importance',
    ),

  trainModel: (body: { n_users: number; n_transactions: number; fraud_rate: number; seed: number }) =>
    request<{ status: string; message: string; regenerate_dataset: boolean; metrics: Record<string, unknown> }>(
      '/ml/train',
      { method: 'POST', body: JSON.stringify(body) },
    ),

  // -- settings / docs / audit -------------------------------------------
  settings: () => request<AppSettings>('/settings'),

  patchSettings: (body: { thresholds: Partial<AppSettings['thresholds']> }) =>
    request<AppSettings>('/settings', { method: 'PATCH', body: JSON.stringify(body) }),

  documentation: () => request<DocumentationPayload>('/docs'),

  auditLog: (params: { limit?: number; offset?: number; action?: string } = {}) =>
    request<Paginated<AuditEntry>>(`/audit${query(params)}`),

  // -- direct scoring -----------------------------------------------------
  /** The original real-time scoring endpoint, mounted at the API root. */
  scoreTransaction: (body: {
    user_id: string;
    amount: number;
    channel: string;
    country: string;
    merchant_id: string;
    device_id: string;
  }) =>
    request<ScoreResponse>(
      '/score',
      { method: 'POST', body: JSON.stringify(body) },
      true,
    ),
};
