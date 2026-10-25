/**
 * Typed API client.
 *
 * Every request goes through `request()`, so error handling is in one place: a
 * non-2xx response becomes an `ApiError` carrying the backend's `detail` message,
 * which the UI surfaces verbatim rather than showing a generic failure.
 */
import type {
  Account,
  AccountCard,
  AccountList,
  AppSettings,
  AuditEntry,
  CustomerTransaction,
  CustomerTransactionDetail,
  DefenseComparison,
  DefenseConfigInfo,
  DemoCredential,
  DocumentationPayload,
  Incident,
  IncidentDetail,
  ModelEvaluation,
  ModelInfo,
  MonitoringEvent,
  NetworkGraph,
  NotificationFeed,
  OverviewPayload,
  Paginated,
  Payee,
  Replay,
  ScenarioInfo,
  ScoreResponse,
  SecurityCentre,
  SessionIssued,
  SessionStatus,
  SimulatedLogin,
  SimulationRun,
  SimulationRunDetail,
  Transaction,
  TransactionDetail,
  TransactionFilters,
  TransferRequest,
  TransferResult,
} from './types';
import { getDeviceId, getToken, notifyUnauthorized } from './session';

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
  // The token is read fresh on every call, so signing out in one tab takes effect
  // in the next request this tab makes rather than at the next page load.
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  if (token) {
    headers.Authorization = `Session ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the RupeeShield API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    // A rejected session is not a generic failure: it means the token that *was*
    // sent no longer resolves, so the app should return to the sign-in page. Only
    // fire when a token was actually presented, otherwise a failed sign-in would
    // look like an expiry.
    if (response.status === 401 && token) {
      notifyUnauthorized();
    }
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

function query(params: Record<string, unknown>): string {
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

  // -- authentication -----------------------------------------------------
  /** The published demo logins. Public: the sign-in page needs them before a session. */
  demoCredentials: () => request<DemoCredential[]>('/auth/demo-credentials'),

  login: (body: { email: string; password: string; device_id: string }) =>
    request<SessionIssued>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),

  session: () => request<SessionStatus>('/auth/session'),

  logout: () =>
    request<{ message: string; revoked: number }>('/auth/logout', { method: 'POST' }),

  // -- notifications ------------------------------------------------------
  notifications: (params: { unread_only?: boolean; limit?: number; offset?: number } = {}) =>
    request<NotificationFeed>(`/notifications${query(params)}`),

  unreadCount: () => request<{ unread: number }>('/notifications/unread-count'),

  markNotificationRead: (notificationId: string) =>
    request<{ message: string }>(`/notifications/${notificationId}/read`, { method: 'POST' }),

  markAllNotificationsRead: () =>
    request<{ message: string }>('/notifications/read-all', { method: 'POST' }),

  // -- customer banking ---------------------------------------------------
  bankingAccounts: () => request<AccountList>('/banking/accounts'),

  bankingAccount: (accountId: string) => request<AccountCard>(`/banking/accounts/${accountId}`),

  payees: () => request<Payee[]>('/banking/payees'),

  bankingTransactions: (params: TransactionFilters = {}) =>
    // Spread into a plain object so a named interface, which has no index
    // signature, can travel through `query` like every other filter set.
    request<Paginated<CustomerTransaction>>(`/banking/transactions${query({ ...params })}`),

  bankingTransaction: (transactionId: string) =>
    request<CustomerTransactionDetail>(`/banking/transactions/${transactionId}`),

  createTransfer: (body: TransferRequest) =>
    request<TransferResult>('/banking/transfers', {
      method: 'POST',
      // The device id travels as a header, exactly as the browser's own device
      // fingerprint does, and is what the new-device rule reads.
      headers: { 'X-Device-Id': getDeviceId() },
      body: JSON.stringify(body),
    }),

  securityCentre: () => request<SecurityCentre>('/banking/security-centre'),

  simulateLogin: (deviceId?: string) =>
    request<SimulatedLogin>(
      `/banking/security-centre/simulate-login${query({ device_id: deviceId })}`,
      { method: 'POST' },
    ),

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
