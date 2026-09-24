/** Response shapes for the backend API. Mirrors backend/app/schemas. */

export type TrainingStatus = "not_started" | "passed" | "needs_retry";
export type TaskStatus = "assigned" | "in_progress" | "paused" | "blocked" | "done" | "cancelled";
export type TaskAction = "start" | "pause" | "resume" | "block" | "unblock" | "complete";
export type Severity = "warning" | "critical";
export type IncidentStatus = "open" | "acknowledged" | "resolved";
export type ScenarioName = "proximity" | "sensor_fault" | "overload" | "seatbelt" | "idling" | "tilt" | "end_shift";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: "operator" | "admin";
  user_id: string;
  operator_id: string | null;
  offline: boolean;
}

export interface Shift {
  shift_id: string;
  machine_id: string;
  operator_id: string;
  operator_name: string;
  scheduled_start: string;
  scheduled_end: string;
  weather_forecast: string;
  machine_model: string;
  machine_type: string;
  bucket_capacity: number;
  tilt_limit_degrees: number;
  rated_max_rpm: number;
}

export interface Task {
  task_id: string;
  shift_id: string;
  task_type: string;
  material_type: string;
  target_quantity: number;
  quantity_unit: string;
  completed_quantity: number;
  status: TaskStatus;
  scheduled_start: string;
  scheduled_end: string | null;
  eta_minutes: number | null;
  eta_from_history: boolean;
  /** Why the machine revised the ETA: "weather" or "pace"; null when not revised. */
  eta_revision_reason: string | null;
  actual_start: string | null;
  actual_end: string | null;
}

export interface Incident {
  incident_id: string;
  incident_type: string;
  peak_severity: Severity;
  status: IncidentStatus;
  source: string;
  event_start: string;
  event_end: string | null;
  escalation_reason: string | null;
  description: string | null;
  acknowledged_at: string | null;
}

/** Latest live sensor state. Every sensor field is null while live is false. */
export interface MachineStatus {
  machine_id: string;
  live: boolean;
  parked: boolean;
  timestamp: string | null;
  engine_running: boolean | null;
  engine_rpm: number | null;
  engine_hours: number | null;
  fuel_used: number | null;
  hydraulic_active: boolean | null;
  machine_speed: number | null;
  payload_pct: number | null;
  seatbelt_status: "fastened" | "unfastened" | null;
  seat_occupied: boolean | null;
  park_brake: boolean | null;
  gear_state: string | null;
  proximity_distance: number | null;
  proximity_sensor_ok: boolean | null;
  ambient_temp: number | null;
  visibility: number | null;
  tilt_angle: number | null;
  task_id: string | null;
}

export interface Coaching {
  event_id: string;
  event_type: string;
  message: string;
  magnitude: number;
  baseline_value: number | null;
  start: string;
  end: string;
  module_id: string | null;
  module_title: string | null;
}

export interface Snapshot {
  shift: Shift | null;
  tasks: Task[];
  incidents: Incident[];
  coaching: Coaching[];
  status: MachineStatus;
}

/** Raw telemetry tick pushed over the WebSocket. */
export interface TelemetryTick {
  timestamp: string;
  machine_id: string;
  task_id: string | null;
  engine_running: boolean;
  engine_rpm: number;
  engine_hours: number;
  fuel_used: number;
  hydraulic_active: boolean;
  machine_speed: number;
  payload_pct: number;
  seatbelt_status: "fastened" | "unfastened";
  seat_occupied: boolean;
  park_brake: boolean;
  gear_state: string;
  proximity_distance: number | null;
  proximity_sensor_ok: boolean;
  ambient_temp: number;
  visibility: number;
  tilt_angle: number;
}

export interface ProgressUpdate {
  task_id: string;
  status: TaskStatus;
  completed_quantity: number;
  target_quantity: number;
  target_reached: boolean;
  eta_minutes: number | null;
  eta_revision_reason: string | null;
}

export interface RecommendationNotice {
  module_id: string;
  module_title: string;
}

export type ServerMessage =
  | { type: "state"; data: Snapshot }
  | { type: "telemetry"; data: TelemetryTick }
  | { type: "incident"; data: { incident_id: string | null; event: string } }
  | { type: "progress"; data: ProgressUpdate }
  | { type: "coaching"; data: { event_id: string; recommendation: RecommendationNotice | null } }
  | { type: "recommendation"; data: RecommendationNotice }
  | { type: "safety_degraded"; data: { rule: string; reason: "rule_error" | "sensor_fault" } }
  | { type: "safety_restored"; data: { rule: string } }
  | { type: "ping" };

export interface SimStatus {
  running: boolean;
  sim_time: string;
  selected_speed: number;
  effective_speed: number;
  machine_ids: string[];
  scenarios: { scenario: string; machine_id: string | null; end: string }[];
}

export interface SystemStatus {
  status: string;
  sim_time: string;
  cloud_reachable: boolean;
  app_env: string;
  cloud_outbox_pending: number;
  edge_outbox_pending: number;
  /** Records waiting in the edge spool for Kafka; null when streaming is off. */
  stream_spool_pending?: number | null;
}

export interface Health {
  status: string;
  sim_time: string;
  cloud_reachable: boolean;
}

export interface ModuleSummary {
  module_id: string;
  title: string;
  format: string;
  duration_min: number;
  status: TrainingStatus;
  recommended: boolean;
}

export interface ModuleListResponse {
  parked: boolean;
  pass_pct: number;
  modules: ModuleSummary[];
}

export interface QuizQuestion {
  question: string;
  options: string[];
}

export interface ModuleContentResponse {
  module_id: string;
  title: string;
  duration_min: number;
  status: TrainingStatus;
  body_markdown: string;
  quiz: QuizQuestion[];
}

export interface QuizQuestionResult {
  correct: boolean;
  correct_option: number;
  explanation: string;
}

export interface QuizResultResponse {
  module_id: string;
  score: number;
  passed: boolean;
  pass_pct: number;
  status: TrainingStatus;
  questions: QuizQuestionResult[];
}

export interface ManualSearchHit {
  manual_id: string;
  manual_title: string;
  machine_type: string | null;
  section_title: string;
  text: string;
  score: number;
}

export interface ManualSearchResponse {
  query: string;
  results: ManualSearchHit[];
}

export interface EmergencyItem {
  emergency_id: string;
  title: string;
  summary: string;
  steps: string[];
}

export interface EmergencyGuide {
  notice: string;
  items: EmergencyItem[];
}

// ---------------------------------------------------------------------------
// Admin (cloud)
// ---------------------------------------------------------------------------

export type Delivery = "queued" | "delivered";
export type WeatherCategory = "clear" | "cloudy" | "rain" | "fog" | "storm";
export type TaskType = "excavation" | "material_loading" | "trenching";
export type QuantityUnit = "m3" | "loads";
export type MaterialType = "clay" | "sandy_soil" | "gravel" | "rock" | "mixed_fill" | "topsoil";

export interface AdminOperator {
  operator_id: string;
  operator_name: string;
  qualifications: { machine_type: string; skill_level: string }[];
  /** null when the operator has no sign-in account and cannot see assigned work. */
  username: string | null;
}

export interface OperatorAccountCreate {
  username: string;
  password: string;
  pin: string;
}

export interface OperatorAccount {
  operator_id: string;
  user_id: string;
  username: string;
  /** Offline sign-ins issued for the operator's current and upcoming shifts. */
  credentials_issued: number;
}

export interface AdminMachine {
  machine_id: string;
  machine_model: string;
  machine_type: string;
  machine_status: "available" | "maintenance";
  machine_age: number;
  bucket_capacity: number;
  service: ServiceSuggestion;
}

export interface AdminShift {
  shift_id: string;
  operator_id: string;
  machine_id: string;
  scheduled_start: string;
  scheduled_end: string;
  weather_forecast: string;
  weather_actual: string | null;
  delivery: Delivery;
}

export interface AdminTask {
  task_id: string;
  shift_id: string;
  task_type: TaskType;
  material_type: string;
  target_quantity: number;
  quantity_unit: QuantityUnit;
  completed_quantity: number;
  status: TaskStatus;
  operator_skill_at_assignment: string;
  scheduled_start: string;
  scheduled_end: string | null;
  reassigned_at: string | null;
  actual_start: string | null;
  actual_end: string | null;
  raw_predicted_time: number | null;
  planning_eta: number | null;
  revised_predicted_time: number | null;
  is_fallback: boolean;
  model_version: string | null;
  aggregates_version: string | null;
  delivery: Delivery;
}

export interface AssignmentWarning {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface AssignmentResponse {
  task: AdminTask;
  warnings: AssignmentWarning[];
}

export interface ShiftCreateResponse extends AdminShift {
  warnings: AssignmentWarning[];
}

// ---------------------------------------------------------------------------
// Data pipeline (edge spool -> Kafka -> cloud analytics)
// ---------------------------------------------------------------------------

export type ForwarderState = "disabled" | "stopped" | "link_down" | "connecting" | "broker_down" | "draining" | "idle";
export type ConsumerState = "stopped" | "connecting" | "broker_down" | "running";

export interface PipelineStatus {
  enabled: boolean;
  bootstrap_servers: string;
  topics: string[];
  forwarder: {
    state: ForwarderState;
    sent_total: number;
    failed_total: number;
    dropped_total: number;
    gaps_total: number;
    send_rate: number;
    arrival_rate: number;
    max_rate: number;
    last_sent_at: string | null;
    last_error: string | null;
    last_error_at: string | null;
    retry_in_seconds: number | null;
  };
  spool: {
    total: number;
    by_kind: Record<string, number>;
    by_machine: { machine_id: string; records: number; oldest_event_time: string }[];
    oldest_age_seconds: number | null;
    bytes: number;
    limit: number;
    drain_eta_seconds: number | null;
    write_failures: number;
  };
  consumer: {
    state: ConsumerState;
    received_total: number;
    stored_total: number;
    duplicates_total: number;
    malformed_total: number;
    lag: number | null;
    last_batch_at: string | null;
    last_error: string | null;
    last_error_at: string | null;
  };
  archive: {
    ticks: number;
    machines: number;
    first_ts: string | null;
    last_ts: string | null;
    dropped_total: number;
    recent_gaps: { machine_id: string; from_ts: string; to_ts: string; dropped_count: number; dropped_at: string }[];
    events_by_type: { kind: string; event_type: string; count: number }[];
  };
}

export interface RollupPoint {
  bucket_start: string;
  ticks: number;
  engine_on_ticks: number;
  working_ticks: number;
  idle_ticks: number;
  avg_rpm: number;
  fuel_used: number;
  load_cycles: number;
}

export interface MachineRollup {
  machine_id: string;
  points: RollupPoint[];
}

export interface ShiftCreate {
  operator_id: string;
  machine_id: string;
  scheduled_start: string;
  scheduled_end: string;
  weather_forecast: WeatherCategory;
}

export interface TaskCreate {
  shift_id: string;
  task_type: TaskType;
  target_quantity: number;
  quantity_unit: QuantityUnit;
  material_type: MaterialType;
  scheduled_start?: string;
}

export interface EtaBreakdown {
  task_id: string;
  historical_average_minutes: number;
  raw_predicted_time: number | null;
  buffer_minutes: number;
  planning_eta: number | null;
  revised_predicted_time: number | null;
  revised_at: string | null;
  operator_eta: number | null;
  is_fallback: boolean;
  model_version: string | null;
  aggregates_version: string | null;
  current_model_version: string;
  unusual_features: { feature: string; value: string; description: string }[];
}

export interface SyncConflict {
  conflict_id: string;
  task_id: string;
  conflict_type: "reassign_after_start" | "cancel_after_start";
  edge_actual_start: string;
  cloud_changed_at: string;
  created_at: string;
  resolved: boolean;
}

// ---------------------------------------------------------------------------
// Phase 9: service suggestions, shift summary, explanations
// ---------------------------------------------------------------------------

export type ServiceStatus = "ok" | "due_soon" | "overdue";

export interface ServiceSuggestion {
  status: ServiceStatus;
  engine_hours: number;
  hours_since_service: number;
  hours_remaining: number;
  service_interval_hours: number;
  message: string | null;
}

export interface ShiftReport {
  shift_id: string;
  operator_name: string;
  machine_model: string;
  scheduled_start: string;
  scheduled_end: string;
  source: "edge" | "cloud";
  tasks_done: number;
  tasks_total: number;
  tasks: {
    task_id: string;
    task_type: string;
    status: TaskStatus;
    target_quantity: number;
    completed_quantity: number;
    quantity_unit: string;
    working_minutes: number | null;
  }[];
  time: {
    engine_hours: number;
    working_minutes: number;
    idle_minutes: number;
    idle_ratio: number;
    cycle_count: number;
    fuel_used: number;
  } | null;
  incidents_total: number;
  incidents_critical: number;
  incidents: { type: string; count: number }[];
  behavior: { type: string; count: number }[];
  training: { module_id: string; title: string }[];
}

export interface ExplainResponse extends ManualSearchResponse {
  explanation: string | null;
  notice: string | null;
  model: string | null;
}

export interface DeadLetter {
  message_id: string;
  direction: "cloud_to_edge" | "edge_to_cloud";
  message_type: string;
  entity_id: string | null;
  attempts: number;
  last_error: string | null;
  created_at: string;
  first_failed_at: string | null;
  dead_at: string;
}
