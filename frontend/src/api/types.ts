/**
 * TypeScript mirrors of the FastAPI response schemas (app/schemas/*.py).
 *
 * Kept hand-written rather than code-generated: there are only a handful of
 * contracts and a hand-written mirror is easier to read during a viva than a
 * generated file. Any field that is `| null` in Python is `| null` here too —
 * the backend deliberately reports `null` instead of a fake 0 when a metric is
 * undefined (e.g. precision on an all-negative test slice).
 */

export type Decision = 'ALLOW' | 'FLAG' | 'BLOCK';
export type Severity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type DefenseConfigKey = 'NONE' | 'RULE_BASED' | 'COMBINED';
export type ScenarioKey =
  | 'ACCOUNT_TAKEOVER'
  | 'CREDENTIAL_STUFFING'
  | 'PAYMENT_MANIPULATION'
  | 'SUSPICIOUS_TRANSFER';

export interface Provenance {
  source: string;
  detail: string;
  deterministic: boolean | null;
}

export interface ShapFeature {
  feature: string;
  value: number;
  shap_contribution: number;
}

export interface ScoreResponse {
  transaction_id: string;
  fraud_risk_score: number;
  decision: Decision;
  ml_supervised_score: number | null;
  ml_anomaly_score: number | null;
  rule_heuristic_score: number;
  reasons: string[];
  rules_triggered: string[];
  top_shap_features: ShapFeature[];
  latency_ms: number;
}

export interface Transaction {
  id: string;
  run_id: string | null;
  account_id: string;
  account_label: string | null;
  counterparty_id: string | null;
  counterparty_label: string | null;
  timestamp: string;
  amount_minor: number;
  amount: number;
  currency: string;
  channel: string;
  country: string;
  merchant_id: string;
  device_id: string;
  direction: string;
  status: string;
  scenario: string | null;
  risk_score: number | null;
  decision: Decision | null;
  is_fraud: number | null;
}

export interface TransactionDetail extends Transaction {
  detection: Record<string, unknown> | null;
  explanation: ShapFeature[];
  security_events: Record<string, unknown>[];
  neighborhood: Record<string, unknown>[];
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  returned: number;
}

export interface Account {
  id: string;
  label: string;
  home_country: string;
  preferred_channel: string;
  account_age_days: number;
  balance_minor: number;
  balance: number;
  opening_balance_minor: number;
  risk_status: string;
  is_synthetic: boolean;
  transaction_count: number;
}

export interface ScenarioInfo {
  key: ScenarioKey;
  name: string;
  description: string;
  objective: string;
  risk_level: string;
  default_events: number;
  signature: string[];
}

export interface DefenseConfigInfo {
  key: DefenseConfigKey;
  name: string;
  description: string;
  uses_ml: boolean;
  uses_correlation: boolean;
}

export interface SimulationRunSummary {
  detections?: Record<string, number | null>;
  decisions?: Record<string, number>;
  prevented_value?: number;
  realized_loss?: number;
  [key: string]: unknown;
}

export interface SimulationRun {
  id: string;
  scenario: ScenarioKey;
  scenario_name: string | null;
  defense_config: DefenseConfigKey;
  seed: number;
  requested_events: number;
  generated_events: number;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'STOPPED';
  mode: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  error: string | null;
  summary: SimulationRunSummary;
}

export interface SimulationRunDetail extends SimulationRun {
  incidents: Record<string, unknown>[];
  detection_summary: Record<string, unknown>;
  financial_impact: FinancialImpact | null;
  assumptions: string[];
}

export interface FinancialImpact {
  id: string;
  run_id: string | null;
  incident_id: string | null;
  exposure_minor: number;
  exposure: number;
  unauthorized_minor: number;
  unauthorized: number;
  prevented_minor: number;
  prevented: number;
  /** Legitimate value the defense stopped: the false-positive cost in rupees. */
  interrupted_minor: number;
  interrupted: number;
  disruption_cost_minor: number;
  disruption_cost: number;
  recovery_cost_minor: number;
  recovery_cost: number;
  net_impact_minor: number;
  net_impact: number;
  assumptions: string[];
}

export interface Incident {
  id: string;
  run_id: string | null;
  scenario: ScenarioKey;
  scenario_name: string | null;
  primary_account_id: string;
  primary_account_label: string | null;
  title: string;
  summary: string;
  severity: Severity;
  risk_level: string;
  status: 'OPEN' | 'INVESTIGATING' | 'CONTAINED' | 'CLOSED';
  opened_at: string;
  closed_at: string | null;
  event_count: number;
  transaction_count: number;
  suspected_loss_minor: number;
  suspected_loss: number;
}

export interface IncidentTimelineEntry {
  id: string;
  seq: number;
  timestamp: string;
  kind: 'SECURITY_EVENT' | 'TRANSACTION' | 'DETECTION' | 'DEFENSE';
  ref_id: string | null;
  description: string;
  payload: Record<string, unknown>;
}

export interface IncidentDetail extends Incident {
  notes: string;
  timeline: IncidentTimelineEntry[];
  transactions: Record<string, unknown>[];
  detections: Record<string, unknown>[];
  defense_actions: Record<string, unknown>[];
  financial_impact: FinancialImpact | null;
  triggered_rules: string[];
}

export interface ReplayStep {
  seq: number;
  timestamp: string;
  kind: string;
  description: string;
  decision: Decision | null;
  risk_score: number | null;
  matches_original: boolean;
}

export interface Replay {
  replay_id: string;
  incident_id: string;
  started_at: string;
  finished_at: string;
  steps: ReplayStep[];
  steps_replayed: number;
  divergence_count: number;
  mutates_history: boolean;
  summary: Record<string, unknown>;
}

export interface GraphNode {
  id: string;
  label: string;
  risk_status: string;
  is_primary: boolean;
  balance_minor: number;
  balance: number;
  transaction_count: number;
  total_out_minor: number;
  total_in_minor: number;
  suspicious_connection_count: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  transaction_count: number;
  amount_minor: number;
  amount: number;
  suspicious: boolean;
  max_risk_score: number | null;
}

export interface NetworkGraph {
  run_id: string | null;
  incident_id: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  node_limit: number;
  generated_at: string;
  legend: string[];
}

export interface MonitoringEvent {
  sequence: number;
  timestamp: string;
  run_id: string | null;
  event_type: string;
  account_id: string;
  account_label: string | null;
  amount: number | null;
  amount_minor: number | null;
  decision: Decision | null;
  risk_score: number | null;
  response: string;
  detail: string;
  source: string;
}

export interface LatencyStats {
  samples: number;
  mean_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
}

export interface OverviewStats {
  total_simulated_transactions: number;
  total_value_minor: number;
  total_value: number;
  suspicious_transactions: number;
  blocked_transactions: number;
  flagged_transactions: number;
  open_incidents: number;
  total_incidents: number;
  value_at_risk_minor: number;
  value_at_risk: number;
  loss_prevented_minor: number;
  loss_prevented: number;
  realized_loss_minor: number;
  realized_loss: number;
  detection_latency: LatencyStats;
  active_runs: number;
  completed_runs: number;
  observed_dataset: Record<string, unknown> | null;
  provenance: Record<string, Provenance>;
  generated_at: string;
}

export interface OverviewPayload {
  stats: OverviewStats;
  charts: {
    volume_over_time: { timestamp: string; value: number; extra: Record<string, unknown> }[];
    alerts_by_severity: { key: string; count: number }[];
    decisions_breakdown: { key: string; count: number }[];
    scenario_breakdown: { key: string; count: number }[];
    top_risk_accounts: Record<string, unknown>[];
  };
  recent_events: Record<string, unknown>[];
  recent_incidents: Incident[];
  empty_states: string[];
}

export interface DefenseConfigResult {
  config: DefenseConfigKey;
  name: string;
  detected_events: number;
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number | null;
  recall: number | null;
  false_positive_rate: number | null;
  detection_latency_ms: number | null;
  prevented_count: number;
  prevented_value_minor: number;
  prevented_value: number;
  realized_loss_minor: number;
  realized_loss: number;
  net_impact_minor: number;
  net_impact: number;
  processing_ms: number;
  run_id: string | null;
  notes: string[];
}

export interface DefenseComparison {
  id: string;
  scenario: ScenarioKey;
  scenario_name: string | null;
  seed: number;
  n_events: number;
  label_source: string;
  results: DefenseConfigResult[];
  same_inputs: boolean;
  conclusion: string[];
  /** Caveats specific to this comparison, e.g. ML artefacts missing. */
  notes: string[];
  created_at: string;
}

export interface MetricBlock {
  key: string;
  label: string;
  split: string;
  description: string;
  samples: number | null;
  threshold: number | null;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  pr_auc: number | null;
  roc_auc: number | null;
  false_positive_rate: number | null;
  accuracy: number | null;
  confusion: {
    true_negative: number;
    false_positive: number;
    false_negative: number;
    true_positive: number;
    labels: string[][];
  } | null;
  class_balance: Record<string, number> | null;
  note: string | null;
}

export interface ModelInfo {
  models_available: boolean;
  version: string | null;
  trained_at: string | null;
  feature_order: string[];
  artifacts: { name: string; path: string; exists: boolean; size_bytes: number | null }[];
  calibration: Record<string, number>;
  registered_versions: Record<string, unknown>[];
  setup_instructions: string[];
  shap_available: boolean;
  notes: string[];
}

export interface ModelEvaluation {
  available: boolean;
  model_info: ModelInfo;
  metrics: MetricBlock[];
  pr_curves: Record<
    string,
    { precision: number[]; recall: number[]; thresholds: number[]; average_precision: number | null }
  >;
  feature_importance: { feature: string; importance: number }[];
  class_balance: Record<string, unknown>;
  dataset: Record<string, unknown>;
  split_strategy: string;
  latency: Record<string, number | null>;
  limitations: string[];
  generated_at: string;
}

export interface Thresholds {
  block_threshold: number;
  flag_threshold: number;
  rule_velocity_10min: number;
  rule_velocity_1h: number;
  rule_amount_zscore: number;
  rule_amount_over_avg: number;
  failed_login_burst: number;
  failed_login_window_minutes: number;
  correlation_lookback_minutes: number;
  new_device_high_value_minor: number;
  coordinated_fanout_beneficiaries: number;
}

export interface AppSettings {
  app_name: string;
  app_version: string;
  environment: string;
  database_backend: string;
  model_dir: string;
  data_dir: string;
  default_seed: number;
  max_simulation_events: number;
  rate_limit_requests_per_minute: number;
  enable_api_training: boolean;
  cors_origins: string[];
  thresholds: Thresholds;
  secret_notes: string[];
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  outcome: string;
  details: Record<string, unknown>;
}

export interface DocumentationPayload {
  title: string;
  version: string;
  sections: { key: string; title: string; body: string; bullets: string[] }[];
  formulas: Record<string, unknown>[];
  dataset_provenance: Record<string, unknown>;
  ethics: string[];
  troubleshooting: { problem: string; resolution: string }[];
  commands: { label: string; command: string }[];
}

// ---------------------------------------------------------------------------
// Authentication (app/schemas/auth.py)
// ---------------------------------------------------------------------------

export type UserRole = 'CUSTOMER' | 'ANALYST';

export interface CustomerProfile {
  phone_masked: string;
  city: string;
  kyc_status: string;
}

export interface SessionUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_demo: boolean;
  last_login_at: string | null;
  profile: CustomerProfile;
  account_ids: string[];
  account_count: number;
}

/** Returned once, by `POST /api/auth/login`. The token is never echoed again. */
export interface SessionIssued {
  token: string;
  issued_at: string;
  expires_at: string;
  user: SessionUser;
}

export interface SessionStatus {
  issued_at: string;
  expires_at: string;
  user: SessionUser;
}

export interface DemoCredential {
  role: UserRole;
  label: string;
  email: string;
  password: string;
  description: string;
}

// ---------------------------------------------------------------------------
// Notifications (app/schemas/notification.py)
// ---------------------------------------------------------------------------

export interface NotificationItem {
  id: string;
  kind: string;
  severity: Severity;
  title: string;
  body: string;
  entity_type: string;
  entity_id: string | null;
  created_at: string;
  read: boolean;
  read_at: string | null;
}

export interface NotificationFeed extends Paginated<NotificationItem> {
  unread: number;
}

// ---------------------------------------------------------------------------
// Customer banking (app/schemas/banking.py)
// ---------------------------------------------------------------------------

export interface BalanceSummary {
  account_count: number;
  currency: string;
  total_balance_minor: number;
  total_balance: number;
  held_minor: number;
  held: number;
  available_minor: number;
  available: number;
}

export interface AccountCard {
  id: string;
  label: string;
  masked_number: string;
  account_type: string;
  currency: string;
  status: 'ACTIVE' | 'UNDER_REVIEW';
  status_label: string;
  balance_minor: number;
  balance: number;
  opening_balance_minor: number;
  home_country: string;
  preferred_channel: string;
  account_age_days: number;
  risk_status: string;
  transaction_count: number;
  suspicious_count: number;
  pending_count: number;
  last_activity_at: string | null;
  is_synthetic: boolean;
  is_primary: boolean;
}

export interface AccountList {
  items: AccountCard[];
  summary: BalanceSummary;
  total: number;
  limit: number;
  offset: number;
  returned: number;
}

export interface CustomerTransaction {
  id: string;
  timestamp: string;
  description: string;
  direction: 'DEBIT' | 'CREDIT';
  sender: string | null;
  recipient: string | null;
  counterparty_id: string | null;
  counterparty_label: string | null;
  account_id: string;
  account_label: string | null;
  account_masked_number: string;
  amount_minor: number;
  amount: number;
  signed_amount_minor: number;
  signed_amount: number;
  currency: string;
  channel: string;
  status: string;
  status_label: string;
  decision: Decision | null;
  risk_score: number | null;
  risk_level: string | null;
  suspicious: boolean;
  detection_id: string | null;
  incident_id: string | null;
  is_fraud: number | null;
}

export interface CustomerIncident {
  id: string;
  title: string;
  status: string;
  severity: Severity;
  risk_level: string;
  scenario: ScenarioKey | null;
  scenario_name: string;
  primary_account_id: string;
  primary_account_label: string | null;
  opened_at: string;
  closed_at: string | null;
  event_count: number;
  transaction_count: number;
  suspected_loss_minor: number;
  suspected_loss: number;
}

export interface CustomerTransactionDetail extends CustomerTransaction {
  detection: Record<string, unknown> | null;
  explanation: ShapFeature[];
  security_events: Record<string, unknown>[];
  incident: CustomerIncident | null;
  related: Record<string, unknown>[];
}

export interface TransferLeg {
  account_id: string;
  label: string;
  direction: 'DEBIT' | 'CREDIT';
  amount_minor: number;
  amount: number;
  balance_minor: number;
  balance: number;
  masked_number: string;
}

export interface TransferResult {
  transfer_id: string;
  status: string;
  status_label: string;
  decision: Decision;
  risk_score: number;
  reasons: string[];
  rules_triggered: string[];
  message: string;
  duplicate: boolean;
  settled: boolean;
  amount_minor: number;
  amount: number;
  currency: string;
  description: string;
  created_at: string;
  source: TransferLeg;
  recipient: TransferLeg;
  transactions: CustomerTransaction[];
  notification_id: string | null;
}

export interface Payee {
  id: string;
  label: string;
  masked_number: string;
  account_type: string;
  currency: string;
}

export interface SecurityFactor {
  key: string;
  label: string;
  value: number;
  tone: 'GOOD' | 'ATTENTION' | 'DANGER';
  detail: string;
}

export interface SecurityStatus {
  level: 'GOOD' | 'ATTENTION' | 'ELEVATED';
  label: string;
  summary: string;
}

export interface SignInEvent {
  id: string;
  timestamp: string;
  event_type: string;
  description: string;
  severity: Severity;
  device_id: string | null;
  country: string | null;
  source_ip_hash: string | null;
}

export interface KnownDevice {
  device_id: string;
  country: string | null;
  last_seen: string | null;
  recognised: boolean;
  detail: string;
}

export interface SecurityCentre {
  status: SecurityStatus;
  factors: SecurityFactor[];
  recent_sign_ins: SignInEvent[];
  failed_sign_ins: SignInEvent[];
  devices: KnownDevice[];
  suspicious_transactions: CustomerTransaction[];
  open_incidents: CustomerIncident[];
  security_events: SignInEvent[];
  generated_at: string;
}

export interface SimulatedLogin {
  message: string;
  event: SignInEvent;
  device_id: string;
  country: string;
  notification_id: string | null;
}

export interface TransferRequest {
  source_account_id: string;
  recipient_account_id: string;
  amount: string;
  description: string;
  idempotency_key: string | null;
}

export interface TransactionFilters {
  account_id?: string;
  search?: string;
  status?: string;
  direction?: string;
  suspicious?: boolean;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}
