// DesktopMate 后端 API 客户端。
// baseUrl/token 优先来自 Electron preload（window.desktopMate），否则读 Vite 环境变量。

export interface RuntimeInfo {
  service: string;
  version: string;
  auth_enabled: boolean;
  features: Record<string, boolean>;
  model?: {
    provider: string;
    name: string;
    api_key_source: "settings" | "env" | "none" | string;
    vision_enabled: boolean;
  };
  speech?: SpeechStatus;
}

export interface HealthInfo {
  status: string;
  service: string;
  version: string;
  auth_enabled: boolean;
}

export interface SessionRecord {
  id: string;
  title: string;
  mode: "chat" | "agent" | "plan";
  archived: boolean;
  project_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  text: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ArtifactItem {
  id: string;
  kind: string;
  title: string;
  state: "draft" | "saved" | "archived";
  source_session_id?: string | null;
  external_ref?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  content: string;
  version: number;
  version_state?: "draft" | "saved" | "superseded" | null;
  pending_version?: number | null;
  current_version?: number | null;
}

export interface KnowledgeDocument {
  id: string;
  name: string;
  type: string;
  size: number;
  collection_id: string | null;
  status: "queued" | "parsing" | "indexed" | "failed";
  chunk_count: number;
  error: string | null;
  original_path?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface ProfileSettings {
  display_name: string;
  data_dir: string;
}

export interface ModelSettings {
  provider: string;
  model: string;
  temperature: number;
  context_length: number;
}

export interface AvailableModelsResult {
  provider: string;
  models: string[];
  error?: string;
}

export interface ConfiguredModel {
  provider: string;
  model: string;
  verified_at: string;
}

export interface ApiKeyTestResult {
  ok: boolean;
  provider: string;
  message: string;
  models?: string[];
}

export interface ModelTestResult {
  ok: boolean;
  provider: string;
  model: string;
  message: string;
  configured_model?: ConfiguredModel;
}

export interface ApiKeyRecord {
  provider: string;
  has_key: boolean;
  masked: string | null;
  updated_at: string | null;
  verified_at: string | null;
}

export interface SkillItem {
  name: string;
  description: string;
  risk_level: string;
  requires_permission: boolean;
  enabled: boolean;
  builtin?: boolean;
  version?: string;
  type?: "prompt" | "tool" | string;
  prompt?: string;
}

export interface RuntimeStatus {
  active_runs: number;
  last_run_status: "running" | "completed" | "failed" | null;
  last_message: string | null;
  pending_permissions: number;
  due_reminders: number;
}

export interface RuntimeEvent {
  id: string;
  type: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export type ReminderStatus = "active" | "paused" | "done" | "deleted";
export type ReminderScheduleType = "once" | "daily" | "weekly";

export interface ReminderItem {
  id: string;
  title: string;
  body: string | null;
  schedule_type: ReminderScheduleType;
  due_at: string | null;
  time_of_day: string | null;
  timezone: string;
  status: ReminderStatus;
  created_at: string;
  updated_at: string;
  last_fired_at: string | null;
}

export interface ReminderInput {
  title: string;
  body?: string;
  schedule_type: ReminderScheduleType;
  due_at?: string;
  time_of_day?: string;
  timezone: string;
}

export type FileEntry = {
  name: string;
  type: "dir" | "file";
  size: number;
  modified_at: string;
};

export interface ProjectRecord {
  id: string;
  name: string;
  path: string;
  created_at: string;
  updated_at: string;
  exists: boolean;
}

export interface ProjectCreateInput {
  name: string;
  path: string;
}

export interface FsReadResult {
  path: string;
  name: string;
  size: number;
  content: string;
}

export interface FsWriteResult {
  path: string;
  size: number;
}

export type TaskItem = {
  id: string;
  title: string;
  description?: string;
  due_at?: string;
  reminder_id?: string;
  category?: string;
  priority?: "low" | "normal" | "high";
  next_action?: string;
  source_session_id?: string;
  status: "todo" | "in_progress" | "done";
  created_at: string;
  completed_at?: string;
};

export interface TaskDraftConfirmation {
  session_id: string;
  title: string;
  description?: string;
  due_at?: string;
  timezone: string;
  create_reminder: boolean;
  category?: string;
  priority?: "low" | "normal" | "high";
  next_action?: string;
  reminder_message?: string;
  memory_summary?: string;
}

export interface TaskDraftConfirmationResult {
  task: TaskItem;
  reminder: ReminderItem | null;
  memory: { scope: string; key: string; value: string } | null;
}

export interface SpeechStatus {
  enabled: boolean;
  model: string;
  device: string;
  language?: string;
  model_cached: boolean;
  model_dir?: string;
}

export type SpeechPreloadJobStatus = "queued" | "downloading" | "loading" | "ready" | "failed";

export interface SpeechPreloadJob {
  status: SpeechPreloadJobStatus;
  error?: string;
}

export interface StorageUsage {
  data_dir: string;
  database_bytes: number;
  uploads_bytes: number;
  knowledge_bytes: number;
  traces_bytes: number;
}

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

let cachedBaseUrl: string | null = null;
let cachedToken: string | null = null;

export async function resolveAgentConfig(): Promise<{ baseUrl: string; token: string }> {
  if (cachedBaseUrl !== null && cachedToken !== null) {
    return { baseUrl: cachedBaseUrl, token: cachedToken };
  }
  let baseUrl = "http://127.0.0.1:8000";
  let token = "";
  if (window.desktopMate) {
    baseUrl = (await window.desktopMate.getAgentServiceUrl()) || baseUrl;
    token = await window.desktopMate.getAgentAuthToken();
  } else {
    baseUrl = import.meta.env.VITE_AGENT_SERVICE_URL || baseUrl;
    token = import.meta.env.VITE_AGENT_AUTH_TOKEN || "";
  }
  cachedBaseUrl = baseUrl.replace(/\/+$/, "");
  cachedToken = token;
  return { baseUrl: cachedBaseUrl, token: cachedToken };
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { baseUrl, token } = await resolveAgentConfig();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined)
  };
  if (token) {
    headers["X-DesktopMate-Token"] = token;
  }
  const response = await fetch(`${baseUrl}${path}`, { ...options, headers });
  if (!response.ok) {
    let code = `http_${response.status}`;
    let message = response.statusText;
    try {
      const body = (await response.json()) as {
        error?: { code?: string; message?: string };
      };
      if (body.error?.code) {
        code = body.error.code;
      }
      if (body.error?.message) {
        message = body.error.message;
      }
    } catch {
      // 非 JSON 响应保留默认错误信息
    }
    throw new ApiError(code, message, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// ---------- Runtime / health ----------

export function fetchHealth(): Promise<HealthInfo> {
  return apiFetch<HealthInfo>("/health");
}

export function fetchRuntime(): Promise<RuntimeInfo> {
  return apiFetch<RuntimeInfo>("/api/runtime");
}

export function fetchRuntimeStatus(): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>("/api/runtime/status");
}

export function fetchRuntimeEvents(since?: string) {
  const query = since ? `?since=${encodeURIComponent(since)}` : "";
  return apiFetch<{ events: RuntimeEvent[]; next_cursor: string }>(`/api/runtime/events${query}`);
}

// ---------- Sessions ----------

export function fetchSessions(scope: "recent" | "all" | "archived" = "recent") {
  return apiFetch<{ sessions: SessionRecord[] }>(`/api/sessions?scope=${scope}`);
}

export function createSession(title: string, mode: string) {
  return apiFetch<SessionRecord>("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, mode })
  });
}

export function fetchSessionMessages(sessionId: string) {
  return apiFetch<{ messages: SessionMessage[] }>(`/api/sessions/${sessionId}/messages`);
}

export function patchSession(
  sessionId: string,
  patch: { title?: string; archived?: boolean }
) {
  return apiFetch<SessionRecord>(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch)
  });
}

export function deleteSession(sessionId: string) {
  return apiFetch<{ deleted: boolean }>(`/api/sessions/${sessionId}`, { method: "DELETE" });
}

// ---------- Workspace artifacts ----------

export function fetchArtifacts(includeDrafts = false) {
  return apiFetch<{ artifacts: ArtifactItem[] }>(`/api/artifacts?include_drafts=${includeDrafts}`);
}

export function fetchArtifact(artifactId: string) {
  return apiFetch<ArtifactItem>(`/api/artifacts/${encodeURIComponent(artifactId)}`);
}

export function createArtifact(payload: {
  kind: string;
  title: string;
  content?: string;
  source_session_id?: string;
  external_ref?: string;
  metadata?: Record<string, unknown>;
}) {
  return apiFetch<ArtifactItem>("/api/artifacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export function confirmArtifact(artifactId: string) {
  return apiFetch<ArtifactItem>(`/api/artifacts/${encodeURIComponent(artifactId)}/confirm`, {
    method: "POST"
  });
}

export function cancelRun(runId: string) {
  return apiFetch<{ cancelled: boolean }>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST"
  });
}

// ---------- Knowledge ----------

export function fetchDocuments(params: { collection?: string; status?: string; q?: string } = {}) {
  const query = new URLSearchParams();
  if (params.collection) {
    query.set("collection", params.collection);
  }
  if (params.status) {
    query.set("status", params.status);
  }
  if (params.q) {
    query.set("q", params.q);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<{ documents: KnowledgeDocument[] }>(`/api/knowledge/documents${suffix}`);
}

export function fetchDocument(documentId: string) {
  return apiFetch<KnowledgeDocument>(`/api/knowledge/documents/${encodeURIComponent(documentId)}`);
}

export function uploadDocument(file: File, collectionId?: string) {
  const form = new FormData();
  form.append("file", file);
  if (collectionId) {
    form.append("collection_id", collectionId);
  }
  return apiFetch<KnowledgeDocument>("/api/knowledge/documents", {
    method: "POST",
    body: form
  });
}

export function deleteDocument(documentId: string) {
  return apiFetch<{ deleted: boolean }>(`/api/knowledge/documents/${documentId}`, {
    method: "DELETE"
  });
}

export function reindexDocument(documentId: string) {
  return apiFetch<KnowledgeDocument>(`/api/knowledge/documents/${documentId}/reindex`, {
    method: "POST"
  });
}

export function fetchCollections() {
  return apiFetch<{ collections: KnowledgeCollection[] }>("/api/knowledge/collections");
}

// ---------- Settings ----------

export function fetchProfile() {
  return apiFetch<ProfileSettings>("/api/settings/profile");
}

export function saveProfile(profile: ProfileSettings) {
  return apiFetch<ProfileSettings>("/api/settings/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile)
  });
}

export function fetchModels() {
  return apiFetch<ModelSettings>("/api/settings/models");
}

export function saveModels(models: ModelSettings) {
  return apiFetch<ModelSettings>("/api/settings/models", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(models)
  });
}

export function fetchAvailableModels(provider: string) {
  return apiFetch<AvailableModelsResult>(
    `/api/settings/models/available?provider=${encodeURIComponent(provider)}`
  );
}

export function fetchConfiguredModels() {
  return apiFetch<{ models: ConfiguredModel[] }>("/api/settings/models/configured");
}

export function testModel(provider: string, model: string) {
  return apiFetch<ModelTestResult>("/api/settings/models/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model })
  });
}

export function deleteConfiguredModel(provider: string, model: string) {
  return apiFetch<{ deleted: boolean }>(
    `/api/settings/models/configured?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`,
    { method: "DELETE" }
  );
}

export function fetchApiKeys() {
  return apiFetch<{ keys: ApiKeyRecord[] }>("/api/settings/api-keys");
}

export function saveApiKey(provider: string, apiKey: string) {
  return apiFetch<ApiKeyRecord>("/api/settings/api-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey })
  });
}

export function deleteApiKey(provider: string) {
  return apiFetch<{ deleted: boolean }>(`/api/settings/api-keys/${encodeURIComponent(provider)}`, {
    method: "DELETE"
  });
}

export function testApiKey(provider: string, apiKey?: string) {
  return apiFetch<ApiKeyTestResult>(
    "/api/settings/api-keys/test",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, api_key: apiKey })
    }
  );
}

export function fetchSkills() {
  return apiFetch<{ skills: SkillItem[] }>("/api/skills");
}

export function patchSkill(name: string, enabled: boolean) {
  return apiFetch<SkillItem>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
}

export function importSkillPackage(file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<{ skill: SkillItem }>("/api/skills/import", {
    method: "POST",
    body: form
  });
}

export function deleteSkill(name: string) {
  return apiFetch<{ deleted: boolean }>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "DELETE"
  });
}

// ---------- Reminders ----------

export function fetchReminders(status: ReminderStatus | "all" = "active") {
  return apiFetch<{ reminders: ReminderItem[] }>(`/api/reminders?status=${status}`);
}

export function createReminder(reminder: ReminderInput) {
  return apiFetch<ReminderItem>("/api/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(reminder)
  });
}

export function patchReminder(
  reminderId: string,
  patch: Partial<ReminderInput & { status: ReminderStatus }>
) {
  return apiFetch<ReminderItem>(`/api/reminders/${encodeURIComponent(reminderId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch)
  });
}

export function dismissReminder(reminderId: string) {
  return apiFetch<ReminderItem>(`/api/reminders/${encodeURIComponent(reminderId)}/dismiss`, {
    method: "POST"
  });
}

export function completeReminder(reminderId: string) {
  return apiFetch<ReminderItem>(`/api/reminders/${encodeURIComponent(reminderId)}/complete`, {
    method: "POST"
  });
}

export function deleteReminder(reminderId: string) {
  return apiFetch<{ deleted: boolean }>(`/api/reminders/${encodeURIComponent(reminderId)}`, {
    method: "DELETE"
  });
}

// ---------- Files ----------

export interface FsRoot {
  path: string;
  name: string;
  exists: boolean;
}

export interface FsTreeDirectory {
  name: string;
  path: string;
}

export interface FsTreeFile {
  name: string;
  path: string;
  size: number;
}

export interface FsTree {
  path: string;
  dirs: FsTreeDirectory[];
  files: FsTreeFile[];
}

export interface FsContextExtension {
  ext: string;
  count: number;
}

export interface FsContext {
  path: string;
  name: string;
  tree: string;
  readme: string;
  extensions: FsContextExtension[];
  total_files: number;
}

export function fetchFsRoots(): Promise<{ roots: FsRoot[] }> {
  return apiFetch<{ roots: FsRoot[] }>("/api/fs/roots");
}

export function fetchFsTree(path: string): Promise<FsTree> {
  return apiFetch<FsTree>(`/api/fs/tree?path=${encodeURIComponent(path)}`);
}

export function fetchFsContext(path: string): Promise<FsContext> {
  return apiFetch<FsContext>(`/api/fs/context?path=${encodeURIComponent(path)}`);
}

export function fetchProjects(): Promise<{ projects: ProjectRecord[] }> {
  return apiFetch<{ projects: ProjectRecord[] }>("/api/projects");
}

export function addProject(input: ProjectCreateInput): Promise<{ project: ProjectRecord }> {
  return apiFetch<{ project: ProjectRecord }>("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export function deleteProject(projectId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE"
  });
}

export function fetchProjectSessions(projectId: string): Promise<{ sessions: SessionRecord[] }> {
  return apiFetch<{ sessions: SessionRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/sessions`);
}

export function createProjectSession(projectId: string): Promise<{ session: SessionRecord }> {
  return apiFetch<{ session: SessionRecord }>(`/api/projects/${encodeURIComponent(projectId)}/sessions`, {
    method: "POST"
  });
}

export function fetchFsRead(path: string): Promise<FsReadResult> {
  return apiFetch<FsReadResult>(`/api/fs/read?path=${encodeURIComponent(path)}`);
}

export function writeFsFile(input: { path: string; content: string }): Promise<FsWriteResult> {
  return apiFetch<FsWriteResult>("/api/fs/write", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export function listFiles(path: string): Promise<{ path: string; parent: string | null; entries: FileEntry[] }> {
  return apiFetch<{ path: string; parent: string | null; entries: FileEntry[] }>(
    `/api/files/list?path=${encodeURIComponent(path)}`
  );
}

export function readFile(
  path: string
): Promise<FsReadResult> {
  return fetchFsRead(path);
}

export interface FilePreview {
  path: string;
  name: string;
  kind: "text" | "pdf" | "docx" | "pptx";
  content: string;
  size: number;
  truncated: boolean;
  modified_at: string;
}

export function previewFile(path: string): Promise<FilePreview> {
  return apiFetch<FilePreview>(`/api/files/preview?path=${encodeURIComponent(path)}`);
}

// ---------- Tasks ----------

export function listTasks(): Promise<{ tasks: TaskItem[] }> {
  return apiFetch<{ tasks: TaskItem[] }>("/api/tasks");
}

export function createTask(input: { title: string; description?: string; due_at?: string; reminder_id?: string; category?: string; priority?: "low" | "normal" | "high"; next_action?: string; source_session_id?: string }): Promise<TaskItem> {
  return apiFetch<TaskItem>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export function confirmTaskDraft(input: TaskDraftConfirmation): Promise<TaskDraftConfirmationResult> {
  return apiFetch<TaskDraftConfirmationResult>("/api/tasks/confirm-draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export function updateTask(
  id: string,
  input: { status?: string; title?: string; description?: string | null; due_at?: string | null; reminder_id?: string | null; category?: string; priority?: "low" | "normal" | "high"; next_action?: string | null; source_session_id?: string }
): Promise<TaskItem> {
  return apiFetch<TaskItem>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export function deleteTask(id: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
}

// ---------- Speech / storage ----------

export function fetchSpeechStatus() {
  return apiFetch<SpeechStatus>("/api/speech/status");
}

export function preloadSpeechModel() {
  return apiFetch<{ job_id: string }>("/api/speech/preload", { method: "POST" });
}

export function getSpeechPreloadJob(jobId: string) {
  return apiFetch<SpeechPreloadJob>(`/api/speech/preload/${encodeURIComponent(jobId)}`);
}

export function fetchStorageUsage() {
  return apiFetch<StorageUsage>("/api/storage/usage");
}

// ---------- WebSocket chat ----------

export async function openChatSocket(): Promise<WebSocket> {
  const { baseUrl, token } = await resolveAgentConfig();
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/chat";
  if (token) {
    url.searchParams.set("token", token);
  }
  return new Promise<WebSocket>((resolve, reject) => {
    const socket = new WebSocket(url.toString());
    socket.addEventListener("open", () => resolve(socket), { once: true });
    socket.addEventListener(
      "error",
      () => reject(new Error("WebSocket connection failed")),
      { once: true }
    );
  });
}

export async function openRuntimeSocket(): Promise<WebSocket> {
  const { baseUrl, token } = await resolveAgentConfig();
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/runtime";
  if (token) {
    url.searchParams.set("token", token);
  }
  return new Promise<WebSocket>((resolve, reject) => {
    const socket = new WebSocket(url.toString());
    socket.addEventListener("open", () => resolve(socket), { once: true });
    socket.addEventListener(
      "error",
      () => reject(new Error("Runtime WebSocket connection failed")),
      { once: true }
    );
  });
}

// ---------- Voice / attachments ----------

export interface TranscribeResult {
  text: string;
  language: string | null;
  duration: number | null;
}

export function transcribeAudio(blob: Blob): Promise<TranscribeResult> {
  const form = new FormData();
  form.append("audio", blob, "recording.wav");
  return apiFetch<TranscribeResult>("/api/transcribe", {
    method: "POST",
    body: form
  });
}

export interface AttachmentItem {
  id: string;
  filename: string;
  mime: string;
  size: number;
}

export interface AttachmentLocation {
  path: string;
}

export function uploadImages(files: File[]): Promise<{ attachments: AttachmentItem[] }> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiFetch<{ attachments: AttachmentItem[] }>("/api/uploads", {
    method: "POST",
    body: form
  });
}

// 上传文档附件（md/txt/pdf/docx）——与图片同端点，后端按 mime 分流。
// 返回的 attachment id 在发消息时传给 chat，文档内容会被注入对话上下文。
export function uploadDocuments(files: File[]): Promise<{ attachments: AttachmentItem[] }> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiFetch<{ attachments: AttachmentItem[] }>("/api/uploads", {
    method: "POST",
    body: form
  });
}

export async function attachmentUrl(attachmentId: string): Promise<string> {
  const { baseUrl, token } = await resolveAgentConfig();
  const url = new URL(`${baseUrl}/api/attachments/${encodeURIComponent(attachmentId)}`);
  if (token) {
    url.searchParams.set("token", token);
  }
  return url.toString();
}

export function attachmentLocation(attachmentId: string): Promise<AttachmentLocation> {
  return apiFetch<AttachmentLocation>(`/api/attachments/${encodeURIComponent(attachmentId)}/location`);
}
