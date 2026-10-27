<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import * as api from "./api";
import deskbitsLogoUrl from "./assets/deskbits-logo.png";
import petBubbleSpriteUrl from "./assets/pet-bubble-sprite.png";
import petSpriteUrl from "./assets/pet-body-sprite.png";

type PrimaryView = "today" | "files" | "tasks" | "knowledge" | "chat" | "workspace" | "project" | "artifact" | "settings";
type SettingsPanel =
  | "profile"
  | "personalization"
  | "runtime"
  | "reminders"
  | "models"
  | "skills"
  | "keys"
  | "privacy";
type ConversationMode = "chat" | "agent" | "plan";
type ConnectionStatus = "local" | "connecting" | "connected" | "error";
type MessageRole = "user" | "assistant" | "system" | "tool";
type RunStatus = "idle" | "thinking" | "using_tool" | "waiting_approval" | "done" | "failed";
type KnowledgeFilter = "all" | "indexed" | "queued" | "parsing" | "failed";
type PetVariant = "computer" | "cat" | "robot" | "cloud";
type PetBubbleKind = "heart" | "smile" | "idea" | "like";
type PetVoiceState = "idle" | "listening" | "transcribing" | "thinking" | "approval" | "speaking" | "error";
type OutputKind = "document" | "research" | "knowledge" | "task" | "file";
type WorkspaceCategory = "all" | OutputKind;

interface PetOption {
  id: PetVariant;
  label: string;
  position: string;
}

interface PetBubble {
  kind: PetBubbleKind;
  label: string;
  position: string;
}

interface PetPermission {
  requestId: string;
  toolName: string;
  summary: string;
  riskLevel: string;
}

interface Conversation {
  id: string;
  title: string;
  date: string;
  status: RunStatus;
  mode: ConversationMode;
  projectId?: string | null;
  active?: boolean;
}

interface ConversationGroup {
  date: string;
  conversations: Conversation[];
}

interface Citation {
  title: string;
  source_path: string;
  source_type?: "knowledge" | "web";
  url?: string;
  document_id?: string;
  chunk_id?: string;
}

interface NavigationState {
  view: PrimaryView;
  conversationId: string | null;
  projectId: string | null;
  workspaceItemId: string | null;
  knowledgeSearch: string;
  knowledgeFilter: KnowledgeFilter;
  settingsPanel: SettingsPanel;
}

interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  runId?: string;
  meta?: string;
  attachments?: api.AttachmentItem[];
  reasoning?: string;
}

interface TimelineItem {
  id: string;
  label: string;
  detail: string;
  status: "active" | "done" | "waiting" | "failed";
}

interface ConversationOutput {
  id: string;
  kind: OutputKind;
  label?: string;
  title: string;
  summary: string;
  content?: string;
  toolName?: string;
  status: "ready" | "review" | "working";
  updated: string;
  sourceCount?: number;
  path?: string;
  artifactId?: string;
  version?: number;
  externalRef?: string;
  reminderId?: string;
  saved?: boolean;
  taskDraft?: TaskDraft;
}

interface TaskDraft {
  title: string;
  description: string;
  dueAt?: string;
  category: string;
  priority: "low" | "normal" | "high";
  nextAction?: string;
  memorySummary?: string;
  reminder?: {
    dueAt?: string;
    timezone: string;
    message?: string;
  };
}

interface PlanStep {
  id: string;
  title: string;
  detail: string;
  deliverable?: string;
  status: "pending" | "in_progress" | "done";
}

interface ConversationPlan {
  title: string;
  objective: string;
  steps: PlanStep[];
  constraints: string[];
  successCriteria: string[];
  status: "draft" | "executing" | "completed";
  completedSteps: number;
}

interface WorkspaceItem extends ConversationOutput {
  savedAt: string;
  conversationId?: string;
}

interface WorkspaceEditMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  attachments?: api.AttachmentItem[];
}

interface ArtifactContentBlock {
  type: "heading" | "paragraph" | "bullet_list" | "ordered_list";
  text?: string;
  items?: string[];
}

interface WorkspaceContextPayload {
  title: string;
  kind: string;
  content: string;
}

interface FsTreeRow {
  path: string;
  name: string;
  type: "root" | "dir" | "file";
  depth: number;
  exists?: boolean;
  size?: number;
}

interface PermissionRequest {
  requestId: string;
  runId?: string;
  conversationId: string;
  toolCardId: string;
  toolName: string;
  riskLevel: string;
  summary: string;
}

type ToolCallStatus = "waiting_approval" | "approved" | "running" | "completed" | "failed" | "denied";

interface ToolCallCard {
  id: string;
  callId?: string;
  runId?: string;
  requestId?: string;
  toolName: string;
  riskLevel: string;
  args: Record<string, unknown>;
  review?: Record<string, unknown>;
  result?: Record<string, unknown>;
  summary?: string;
  status: ToolCallStatus;
}

interface ServerEvent {
  type: string;
  session_id?: string;
  run_id?: string;
  payload?: Record<string, unknown>;
}

interface RuntimeSocketMessage {
  id?: string;
  type: string;
  created_at?: string;
  payload?: Record<string, unknown>;
}

const activeView = ref<PrimaryView>("chat");
const activeSettingsPanel = ref<SettingsPanel>("profile");
const leftSidebarOpen = ref(true);
const rightSidebarOpen = ref(true);
const agentMode = "agent" as const;
const composerText = ref("");
const composerInput = ref<HTMLTextAreaElement | null>(null);
const artifactComposerInput = ref<HTMLTextAreaElement | null>(null);
interface PendingImage {
  file: File;
  dataUrl: string;
}

interface PendingDocumentAttachment {
  localId: string;
  filename: string;
  mime: string;
  size: number;
  status: "uploading" | "ready" | "error";
  attachment?: api.AttachmentItem;
  error?: string;
}

const pendingImages = ref<PendingImage[]>([]);
const pendingAttachments = ref<PendingDocumentAttachment[]>([]);
const imageUploadError = ref("");
const attachmentToast = ref("");
const conversationSearch = ref("");
const conversationSearchOpen = ref(false);
const knowledgeSearch = ref("");
const knowledgeFilter = ref<KnowledgeFilter>("all");
const selectedModel = ref("");
const isPetMode = new URLSearchParams(window.location.search).get("mode") === "pet";
const selectedPetStorageKey = "deskbits:selectedPet";
const mainPetPositionStorageKey = "deskbits:mainPetPosition";
const petSessionStorageKey = "deskbits:petSessionId";
const petClickDelay = 280;
const petDragHoldDelay = 180;
const petDragThreshold = 5;
const selectedPet = ref<PetVariant>("computer");
const mainPetDock = ref<HTMLElement | null>(null);
const mainPetPosition = ref<{ left: number; top: number } | null>(null);
const mainPetDragging = ref(false);
const petBubble = ref<PetBubble | null>(null);
const petFloatEnabled = ref(true);
const petFloatSettingError = ref("");
const petVoiceState = ref<PetVoiceState>("idle");
const petTranscript = ref("");
const petReply = ref("");
const petError = ref("");
const petPermission = ref<PetPermission | null>(null);
const petSocket = ref<WebSocket | null>(null);
let petClickTimer: number | null = null;
let discardRecordedVoice = false;
let petCollapseTimer: number | null = null;
let petReplyStreaming = false;
let petDragTimer: number | null = null;
let petPointerDown = false;
let petDragReady = false;
let petDragging = false;
let petSuppressNextClick = false;
let petDragPointerId: number | null = null;
let petDragStartX = 0;
let petDragStartY = 0;
let petDragLastX = 0;
let petDragLastY = 0;
let mainPetDragTimer: number | null = null;
let mainPetPointerDown = false;
let mainPetDragReady = false;
let mainPetPointerId: number | null = null;
let mainPetSuppressNextClick = false;
let mainPetDragStartX = 0;
let mainPetDragStartY = 0;
let mainPetDragLastX = 0;
let mainPetDragLastY = 0;
const activeConversationId = ref<string | null>(null);
const navigationHistory = ref<NavigationState[]>([]);
const navigationIndex = ref(-1);
let restoringNavigation = false;
const activeArtifact = ref<ConversationOutput | null>(null);
const pendingArtifactRevisionId = ref<string | null>(null);
const pendingWorkspaceContext = ref<WorkspaceContextPayload | null>(null);
const workspaceCategory = ref<WorkspaceCategory>("all");
const workspaceSearch = ref("");
const workspaceSaveNotice = ref("");
const projectContextOpen = ref(false);
const fsRoots = ref<api.FsRoot[]>([]);
const fsTreeByPath = ref<Record<string, api.FsTree>>({});
const fsExpandedPaths = ref<Record<string, boolean>>({});
const fsSelectedPath = ref("");
const fsContext = ref<api.FsContext | null>(null);
const fsRootsLoading = ref(false);
const fsTreeLoadingPath = ref("");
const fsContextLoading = ref(false);
const fsContextError = ref("");
const projects = ref<api.ProjectRecord[]>([]);
const activeProjectId = ref<string | null>(null);
const expandedProjectIds = ref<Record<string, boolean>>({});
const projectSessionsByProject = ref<Record<string, api.SessionRecord[]>>({});
const projectSessionsLoading = ref<Record<string, boolean>>({});
const projectDialogOpen = ref(false);
const projectDraftName = ref("");
const projectDraftPath = ref("");
const projectAdding = ref(false);
const projectError = ref("");
const projectFileTree = ref<Record<string, api.FsTree>>({});
const projectExpandedPaths = ref<Record<string, boolean>>({});
const activeProjectFile = ref<api.FsReadResult | null>(null);
const activeProjectFilePath = ref("");
const projectFileDraft = ref("");
const projectFileEditMode = ref(false);
const projectFileLoadingPath = ref("");
const projectFileSaving = ref(false);
const projectFileError = ref("");
const projectFileNotice = ref("");
const projectContextCache = ref<Record<string, WorkspaceContextPayload>>({});
const saveConfirmationOpen = ref(false);
const workspaceItems = ref<WorkspaceItem[]>([]);
const pendingWorkspaceItem = ref<WorkspaceItem | null>(null);
const conversationPickerOpen = ref(false);
const activeWorkspaceItem = ref<WorkspaceItem | null>(null);
const workspaceEditText = ref("");
watch(workspaceEditText, resizeActiveComposers);
const taskDetailDraft = ref({ title: "", description: "", dueAt: "", category: "个人", priority: "normal" as "low" | "normal" | "high", nextAction: "" });
const taskDetailNotice = ref("");
const workspaceEditMessages = ref<Record<string, WorkspaceEditMessage[]>>({});
const messagesByConversation = ref<Record<string, ChatMessage[]>>({});
const timelineByConversation = ref<Record<string, TimelineItem[]>>({});
const conversationOutputsByConversation = ref<Record<string, ConversationOutput[]>>({});
const pendingTaskDraftsByConversation = ref<Record<string, ConversationOutput[]>>({});
const plansByConversation = ref<Record<string, ConversationPlan>>({});
// 计划生成中标记：run 开始（thinking）时置 true 隐藏旧计划，
// plan_draft 工具完成或 run 结束时置 false——避免思考期间右侧栏残留旧计划。
const planGeneratingByConversation = ref<Record<string, boolean>>({});
const connectionStatus = ref<ConnectionStatus>("local");
const runInProgress = ref(false);
const isListening = ref(false);
const pendingPermission = ref<PermissionRequest | null>(null);
const toolCardsByConversation = ref<Record<string, ToolCallCard[]>>({});
const activeSocket = ref<WebSocket | null>(null);
const runtimeSocket = ref<WebSocket | null>(null);
const activeRunId = ref<string | null>(null);
const conversations = ref<Conversation[]>([]);
const artifacts = ref<api.ArtifactItem[]>([]);
const documents = ref<api.KnowledgeDocument[]>([]);
const collections = ref<api.KnowledgeCollection[]>([]);
const skills = ref<api.SkillItem[]>([]);
const apiKeys = ref<api.ApiKeyRecord[]>([]);
const reminders = ref<api.ReminderItem[]>([]);
const filePath = ref("/");
const fileParent = ref<string | null>(null);
const fileEntries = ref<api.FileEntry[]>([]);
const selectedFile = ref<api.FilePreview | null>(null);
const previewError = ref("");
const previewModal = ref<api.FilePreview | null>(null);
const previewModalError = ref("");
type AttachmentPreviewMode = "image" | "text" | "pdf" | "office" | "file";
interface AttachmentPreviewState {
  attachment: api.AttachmentItem;
  url: string;
  mode: AttachmentPreviewMode;
  content?: string;
  truncated?: boolean;
  loading: boolean;
  error: string;
}
const attachmentPreview = ref<AttachmentPreviewState | null>(null);
const filesLoading = ref(false);
const filesUnavailable = ref(false);
const tasks = ref<api.TaskItem[]>([]);
const newTaskTitle = ref("");
const newTaskDescription = ref("");
const newTaskDueAt = ref("");
const tasksLoading = ref(false);
const tasksUnavailable = ref(false);
const runtimeStatus = ref<api.RuntimeStatus | null>(null);
const runtimeEvents = ref<api.RuntimeEvent[]>([]);
const runtimeRunStatus = ref<RunStatus>("idle");
const runtimeLastMessage = ref("");
const speechStatus = ref<api.SpeechStatus | null>(null);
const speechPreloadJobId = ref("");
const speechPreloadStatus = ref<api.SpeechPreloadJobStatus | null>(null);
const speechPreloadError = ref("");
const speechPreloadPolling = ref(false);
const storageUsage = ref<api.StorageUsage | null>(null);
const profile = ref<api.ProfileSettings>({ display_name: "Local User", data_dir: "" });
const models = ref<api.ModelSettings>({
  provider: "openai",
  model: "gpt-4.1-mini",
  temperature: 0.4,
  context_length: 128000
});
const availableModels = ref<string[]>([]);
const availableModelsError = ref("");
const availableModelsLoading = ref(false);
const configuredModels = ref<api.ConfiguredModel[]>([]);
const modelCandidate = ref("");
const modelTestResult = ref("");
const modelTestLoading = ref(false);
const healthInfo = ref<api.HealthInfo | null>(null);
const runtimeInfo = ref<api.RuntimeInfo | null>(null);
const backendError = ref("");
const hasBackendToken = ref(false);
const streamingDeltas = ref<Record<string, string>>({});
const thinkingPanelState = ref<Record<string, boolean>>({});
const planStepExpanded = ref<Record<string, boolean>>({});
const planOverviewExpanded = ref<Record<string, boolean>>({});
const citationsByConversation = ref<
  Record<string, Citation[]>
>({});

const apiKeyProvider = ref("openai");
const apiKeyInput = ref("");
const apiKeyTestResult = ref("");
const skillImportStatus = ref("");
const skillImporting = ref(false);
const skillImportProgress = ref(0);
const skillImportError = ref("");
const skillDropActive = ref(false);
const expandedSkillNames = ref<Record<string, boolean>>({});
const skillImportInput = ref<HTMLInputElement | null>(null);
const runtimePanelError = ref("");
const remindersPanelError = ref("");
const profileSaved = ref(false);
const modelsSaved = ref(false);
const apiKeySaved = ref(false);
const reminderSaved = ref(false);
const newReminder = ref<api.ReminderInput>({
  title: "",
  body: "",
  schedule_type: "daily",
  time_of_day: "09:30",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai"
});
const petOptions: PetOption[] = [
  { id: "computer", label: "Computer", position: "0% 0%" },
  { id: "cat", label: "Cat", position: "100% 0%" },
  { id: "robot", label: "Robot", position: "0% 100%" },
  { id: "cloud", label: "Cloud", position: "100% 100%" }
];
const petBubbles: Record<PetBubbleKind, PetBubble> = {
  heart: { kind: "heart", label: "Warm greeting", position: "0% 0%" },
  smile: { kind: "smile", label: "Friendly hello", position: "100% 0%" },
  idea: { kind: "idea", label: "Working on it", position: "100% 100%" },
  like: { kind: "like", label: "Task complete", position: "0% 100%" }
};
let petBubbleTimer: number | null = null;
let idlePetBubbleTimer: number | null = null;
let speechPreloadPollTimer: number | null = null;
let runtimeReconnectTimer: number | null = null;
let runtimeReconnectAttempt = 0;
let runtimeSocketConnecting = false;
let runtimeSocketStopped = false;
let skillImportProgressTimer: number | null = null;

function isPetVariant(value: string | null): value is PetVariant {
  return petOptions.some((pet) => pet.id === value);
}
const chatMessages = computed(() =>
  activeConversationId.value ? messagesByConversation.value[activeConversationId.value] ?? [] : []
);

const messageList = ref<HTMLElement | null>(null);

const isWaitingForFirstResponse = computed(() => {
  if (!runInProgress.value || pendingPermission.value || !activeConversationId.value) {
    return false;
  }
  const hasVisibleThinking = chatMessages.value.some(
    (message) => message.id.startsWith("stream-") && Boolean(message.reasoning?.trim())
  );
  return !hasVisibleThinking && !(streamingDeltas.value[activeConversationId.value] ?? "").trim();
});

const answerProgressLabel = computed(() => {
  if (runStatus.value === "using_tool") return "正在调用工具并整理回答";
  if (runStatus.value === "waiting_approval") return "等待确认后继续";
  return "正在理解请求";
});

function scrollMessagesToBottom(force = false) {
  nextTick(() => {
    const list = messageList.value;
    if (!list) return;
    const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 80;
    if (force || atBottom) {
      list.scrollTop = list.scrollHeight;
    }
  });
}

watch(chatMessages, () => scrollMessagesToBottom());

function resizeComposerTextarea(input: HTMLTextAreaElement | null) {
  if (!input) return;
  const maxHeight = 156;
  input.style.height = "0px";
  input.style.height = `${Math.min(input.scrollHeight, maxHeight)}px`;
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}

function resizeActiveComposers() {
  nextTick(() => {
    resizeComposerTextarea(composerInput.value);
    resizeComposerTextarea(artifactComposerInput.value);
  });
}

function handleComposerInput(event: Event) {
  resizeComposerTextarea(event.target as HTMLTextAreaElement);
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }
  event.preventDefault();
  void sendMessage();
}

watch(composerText, resizeActiveComposers);

function navigationState(): NavigationState {
  return {
    view: activeView.value,
    conversationId: activeConversationId.value,
    projectId: activeProjectId.value,
    workspaceItemId: activeWorkspaceItem.value?.id ?? null,
    knowledgeSearch: knowledgeSearch.value,
    knowledgeFilter: knowledgeFilter.value,
    settingsPanel: activeSettingsPanel.value
  };
}

function sameNavigationState(left: NavigationState | undefined, right: NavigationState): boolean {
  return Boolean(
      left &&
      left.view === right.view &&
      left.conversationId === right.conversationId &&
      left.projectId === right.projectId &&
      left.workspaceItemId === right.workspaceItemId &&
      left.knowledgeSearch === right.knowledgeSearch &&
      left.knowledgeFilter === right.knowledgeFilter &&
      left.settingsPanel === right.settingsPanel
  );
}

function recordNavigationState(): void {
  if (restoringNavigation) return;
  const next = navigationState();
  if (sameNavigationState(navigationHistory.value[navigationIndex.value], next)) return;
  navigationHistory.value = navigationHistory.value.slice(0, navigationIndex.value + 1);
  navigationHistory.value.push(next);
  navigationIndex.value = navigationHistory.value.length - 1;
}

const canNavigateBack = computed(() => navigationIndex.value > 0);
const canNavigateForward = computed(() => navigationIndex.value >= 0 && navigationIndex.value < navigationHistory.value.length - 1);

async function restoreNavigationState(state: NavigationState): Promise<void> {
  restoringNavigation = true;
  activeConversationId.value = state.conversationId;
  conversations.value = conversations.value.map((conversation) => ({
    ...conversation,
    active: conversation.id === state.conversationId
  }));
  activeArtifact.value = null;
  activeWorkspaceItem.value = null;
  activeProjectId.value = state.projectId;
  knowledgeSearch.value = state.knowledgeSearch;
  knowledgeFilter.value = state.knowledgeFilter;
  activeSettingsPanel.value = state.settingsPanel;

  const workspaceItem = state.workspaceItemId
    ? workspaceLibraryItems.value.find((item) => item.id === state.workspaceItemId)
    : undefined;
  if (state.view === "artifact" && workspaceItem) {
    await openWorkspaceItem(workspaceItem);
  } else {
    activeView.value = state.view;
  }
  restoringNavigation = false;
}

async function navigateBack(): Promise<void> {
  if (!canNavigateBack.value) return;
  navigationIndex.value -= 1;
  await restoreNavigationState(navigationHistory.value[navigationIndex.value]);
}

async function navigateForward(): Promise<void> {
  if (!canNavigateForward.value) return;
  navigationIndex.value += 1;
  await restoreNavigationState(navigationHistory.value[navigationIndex.value]);
}

watch(
  [activeView, activeConversationId, activeProjectId, () => activeWorkspaceItem.value?.id, activeSettingsPanel, knowledgeFilter],
  () => recordNavigationState(),
  { flush: "post", immediate: true }
);

watch(activeView, (view) => {
  if (view === "files" && !filesUnavailable.value && fileEntries.value.length === 0) {
    void loadFiles(filePath.value);
  }
  if (view === "tasks" && !tasksUnavailable.value && tasks.value.length === 0) {
    void loadTasks();
  }
  if (view === "workspace" && projectContextOpen.value && fsRoots.value.length === 0) {
    void loadFsRoots();
  }
  if (view === "project" && activeProject.value) {
    void loadProjectRoot(activeProject.value);
  }
});

const activeTimeline = computed(() =>
  activeConversationId.value ? timelineByConversation.value[activeConversationId.value] ?? [] : []
);

const activeCitations = computed(() =>
  activeConversationId.value ? citationsByConversation.value[activeConversationId.value] ?? [] : []
);

const TOOL_LABELS: Record<string, string> = {
  knowledge_search: "知识库检索",
  knowledge_summarize: "知识库总结",
  knowledge_save: "保存到知识库",
  knowledge_refresh: "知识库刷新",
  knowledge_evaluate: "知识库评估",
  artifact_draft: "生成候选文档",
  artifact_revise: "生成候选版本",
  draft_write: "草稿写入",
  search_files: "文件搜索",
  read_file: "读取文件",
  file_diff: "比较文件变更",
  file_patch: "修改文件",
  task_create: "创建待办",
  task_draft: "生成待办候选",
  task_update: "更新待办",
  task_list: "查看待办",
  reminder_create: "创建提醒",
  reminder_set: "设置提醒",
  reminder_list: "查看提醒",
  reminder_delete: "删除提醒",
  delegate_task: "委派子任务",
  delegate_batch: "并行委派子任务",
  delegate_orchestrate: "拆分并执行子任务",
  plan_draft: "生成执行计划"
};

const activeToolCards = computed(() => {
  const cards = activeConversationId.value
    ? toolCardsByConversation.value[activeConversationId.value] ?? []
    : [];
  return cards.filter((card) => card.toolName !== "task_draft");
});

const toolCardsByRunId = computed<Record<string, ToolCallCard[]>>(() => {
  const grouped: Record<string, ToolCallCard[]> = {};
  for (const card of activeToolCards.value) {
    if (!card.runId) continue;
    grouped[card.runId] = [...(grouped[card.runId] ?? []), card];
  }
  return grouped;
});

const toolCardAnchorMessageByRunId = computed<Record<string, string>>(() => {
  const anchors: Record<string, string> = {};
  for (const message of chatMessages.value) {
    if (message.role === "assistant" && message.runId && !anchors[message.runId]) {
      anchors[message.runId] = message.id;
    }
  }
  return anchors;
});

function shouldRenderToolCardsForMessage(message: ChatMessage): boolean {
  if (message.role !== "assistant" || !message.runId) {
    return false;
  }
  if (toolCardAnchorMessageByRunId.value[message.runId] !== message.id) {
    return false;
  }
  const cards = toolCardsByRunId.value[message.runId] ?? [];
  if (cards.length === 0) {
    return false;
  }
  if (runInProgress.value && activeRunId.value === message.runId) {
    return cards.some((card) => card.status === "waiting_approval");
  }
  return true;
}

function toolCardsForMessage(message: ChatMessage): ToolCallCard[] {
  return shouldRenderToolCardsForMessage(message) ? toolCardsByRunId.value[message.runId!] ?? [] : [];
}

function isThinkingExpanded(message: ChatMessage): boolean {
  const explicit = thinkingPanelState.value[message.id];
  if (explicit !== undefined) return explicit;
  return Boolean(
    message.id.startsWith("stream-") &&
      runInProgress.value &&
      message.runId &&
      message.runId === activeRunId.value
  );
}

function toggleThinkingPanel(message: ChatMessage): void {
  thinkingPanelState.value = {
    ...thinkingPanelState.value,
    [message.id]: !isThinkingExpanded(message)
  };
}

const backendWorkspaceItems = computed<WorkspaceItem[]>(() => {
  const artifactItems: WorkspaceItem[] = artifacts.value.map((artifact) => ({
    id: `artifact-${artifact.id}`,
    artifactId: artifact.id,
    kind: artifact.kind as OutputKind,
    label: artifact.version_state === "draft" ? "候选版本" : undefined,
    title: artifact.title,
    summary: artifact.content ? compactText(artifact.content, 140) : "已保存到工作区。",
    content: artifact.content,
    status: artifact.version_state === "draft" ? "review" : "ready",
    updated: displayDate(artifact.updated_at),
    savedAt: displayDate(artifact.created_at),
    conversationId: artifact.source_session_id ?? undefined,
    externalRef: artifact.external_ref ?? undefined,
    version: artifact.version
  }));
  const documentItems: WorkspaceItem[] = documents.value.map((document) => ({
    id: `backend-doc-${document.id}`,
    kind: "knowledge",
    title: document.name,
    summary:
      document.status === "indexed"
        ? `${document.chunk_count} 个片段已索引，可在对话中通过 RAG 检索。`
        : document.status === "failed"
          ? document.error || "索引失败，打开知识库检查详情。"
          : "正在进入个人知识库，索引完成后可用于问答。",
    status: document.status === "indexed" ? "ready" : document.status === "failed" ? "review" : "working",
    updated: displayDate(document.updated_at),
    savedAt: displayDate(document.created_at),
    sourceCount: document.chunk_count,
    path: document.original_path ?? undefined,
    externalRef: document.id
  }));
  const taskItems: WorkspaceItem[] = tasks.value.map((task) => ({
      id: `backend-task-${task.id}`,
      kind: "task",
      label: "待办",
      title: task.title,
      summary: task.description || (task.due_at ? `截止时间：${new Date(task.due_at).toLocaleString("zh-CN")}` : "来自后端任务清单，后续可在对话中继续安排。"),
      status: task.status === "in_progress" ? "working" : task.status === "done" ? "ready" : "review",
      updated: displayDate(task.created_at),
      savedAt: "任务",
      sourceCount: 0,
      externalRef: task.id,
      reminderId: task.reminder_id || undefined
    }));
  return [...artifactItems, ...documentItems, ...taskItems];
});

function dedupeWorkspaceItems(items: WorkspaceItem[]): WorkspaceItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = `${item.kind}:${item.title}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const workspaceLibraryItems = computed(() => {
  return dedupeWorkspaceItems([...workspaceItems.value, ...backendWorkspaceItems.value]);
});

const visibleFsRows = computed<FsTreeRow[]>(() => {
  const rows: FsTreeRow[] = [];
  const addChildren = (parentPath: string, depth: number) => {
    const tree = fsTreeByPath.value[parentPath];
    if (!tree) return;
    for (const dir of tree.dirs) {
      rows.push({ path: dir.path, name: dir.name, type: "dir", depth });
      if (fsExpandedPaths.value[dir.path]) {
        addChildren(dir.path, depth + 1);
      }
    }
    for (const file of tree.files) {
      rows.push({ path: file.path, name: file.name, type: "file", depth, size: file.size });
    }
  };
  for (const root of fsRoots.value) {
    rows.push({ path: root.path, name: root.name, type: "root", depth: 0, exists: root.exists });
    if (root.exists && fsExpandedPaths.value[root.path]) {
      addChildren(root.path, 1);
    }
  }
  return rows;
});

const activeProject = computed<api.ProjectRecord | null>(() =>
  activeProjectId.value ? projects.value.find((project) => project.id === activeProjectId.value) ?? null : null
);

const activeConversationProject = computed<api.ProjectRecord | null>(() => {
  const projectId = currentConversation.value?.projectId ?? activeProjectId.value;
  return projectId ? projects.value.find((project) => project.id === projectId) ?? null : null;
});

const visibleProjectFileRows = computed<FsTreeRow[]>(() => {
  const project = activeProject.value;
  if (!project) return [];
  const rows: FsTreeRow[] = [{ path: project.path, name: project.name, type: "root", depth: 0, exists: project.exists }];
  const addChildren = (parentPath: string, depth: number) => {
    const tree = projectFileTree.value[parentPath];
    if (!tree) return;
    for (const dir of tree.dirs) {
      rows.push({ path: dir.path, name: dir.name, type: "dir", depth });
      if (projectExpandedPaths.value[dir.path]) {
        addChildren(dir.path, depth + 1);
      }
    }
    for (const file of tree.files) {
      rows.push({ path: file.path, name: file.name, type: "file", depth, size: file.size });
    }
  };
  if (project.exists && projectExpandedPaths.value[project.path]) {
    addChildren(project.path, 1);
  }
  return rows;
});

const activeProjectFileCount = computed(() =>
  visibleProjectFileRows.value.filter((row) => row.type === "file").length
);

const activeOutputs = computed<ConversationOutput[]>(() => {
  const outputs = activeConversationId.value
    ? [...(conversationOutputsByConversation.value[activeConversationId.value] ?? [])]
    : [];

  const persistedArtifacts = activeConversationId.value
    ? artifacts.value
      .filter((artifact) => artifact.source_session_id === activeConversationId.value && artifact.version_state === "saved")
      .map<ConversationOutput | null>((artifact) => {
        const output = outputFromArtifact(asRecord(artifact) ?? {});
        return output ? { ...output, saved: true, status: "ready" as const } : null;
      })
      .filter((output): output is ConversationOutput => output !== null)
    : [];
  for (const artifactOutput of persistedArtifacts) {
    const existing = outputs.findIndex((output) => output.artifactId === artifactOutput.artifactId);
    if (existing >= 0) outputs[existing] = { ...outputs[existing], ...artifactOutput };
    else outputs.unshift(artifactOutput);
  }

  const persistedTasks = activeConversationId.value
    ? tasks.value
      .filter((task) => task.source_session_id === activeConversationId.value)
      .map<ConversationOutput>((task) => ({
        id: `task-${task.id}`,
        kind: "task",
        label: "待办",
        title: task.title,
        summary: task.description || (task.due_at ? `截止 ${new Date(task.due_at).toLocaleString("zh-CN")}` : "已加入待办清单"),
        status: task.status === "in_progress" ? "working" : task.status === "done" ? "ready" : "review",
        updated: displayDate(task.created_at),
        externalRef: task.id,
        reminderId: task.reminder_id,
        saved: true
      }))
    : [];
  for (const taskOutput of persistedTasks) {
    const existing = outputs.findIndex((output) => output.externalRef === taskOutput.externalRef);
    if (existing >= 0) outputs[existing] = { ...outputs[existing], ...taskOutput };
    else outputs.unshift(taskOutput);
  }

  return outputs;
});

const activePlan = computed<ConversationPlan | null>(() =>
  activeConversationId.value ? plansByConversation.value[activeConversationId.value] ?? null : null
);

const activeTaskDrafts = computed<ConversationOutput[]>(() => {
  return activeConversationId.value
    ? pendingTaskDraftsByConversation.value[activeConversationId.value] ?? []
    : [];
});

const workspaceCategories = computed(() => [
  { id: "all" as WorkspaceCategory, label: "全部", count: workspaceLibraryItems.value.length },
  { id: "document" as WorkspaceCategory, label: "文档", count: workspaceLibraryItems.value.filter((item) => item.kind === "document").length },
  { id: "research" as WorkspaceCategory, label: "研究", count: workspaceLibraryItems.value.filter((item) => item.kind === "research").length },
  { id: "knowledge" as WorkspaceCategory, label: "知识库", count: workspaceLibraryItems.value.filter((item) => item.kind === "knowledge").length },
  { id: "task" as WorkspaceCategory, label: "待办", count: workspaceLibraryItems.value.filter((item) => item.kind === "task").length },
  { id: "file" as WorkspaceCategory, label: "文件", count: workspaceLibraryItems.value.filter((item) => item.kind === "file").length }
]);

const filteredWorkspaceItems = computed(() => {
  const query = workspaceSearch.value.trim().toLowerCase();
  return workspaceLibraryItems.value.filter((item) => {
    const matchesCategory = workspaceCategory.value === "all" || item.kind === workspaceCategory.value;
    const matchesQuery = !query || `${item.title} ${item.summary}`.toLowerCase().includes(query);
    return matchesCategory && matchesQuery;
  });
});

const workspaceTaskItems = computed(() =>
  filteredWorkspaceItems.value.filter((item) => item.kind === "task")
);

function workspaceTaskStatus(item: WorkspaceItem): api.TaskItem["status"] | ConversationOutput["status"] {
  return workspaceTask(item)?.status ?? item.status;
}

function taskReminderLabel(item: WorkspaceItem): string {
  const reminder = workspaceReminder(item);
  if (!reminder) return "未设置";
  return reminder.status === "active" ? reminderScheduleLabel(reminder) : "已完成";
}

function outputKindLabel(kind: OutputKind, label?: string): string {
  if (label) return label;
  return {
    document: "文档",
    research: "研究",
    knowledge: "知识库",
    task: "待办",
    file: "文件"
  }[kind];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function toolLabel(toolName: string): string {
  return TOOL_LABELS[toolName] ?? toolName;
}

function defaultToolRisk(toolName: string): string {
  if (["draft_write", "file_patch", "knowledge_save", "task_create", "task_update", "reminder_create"].includes(toolName)) {
    return "write";
  }
  return "read";
}

function toolRiskLabel(riskLevel: string): string {
  return {
    read: "只读",
    write: "修改本地数据",
    system: "系统操作",
    external: "外部操作"
  }[riskLevel] ?? "需确认";
}

function toolRiskDescription(riskLevel: string): string {
  return {
    read: "只读取信息，不会修改本地内容。",
    write: "将修改本地数据；请确认目标和内容后继续。",
    system: "将影响本机系统或应用状态；请谨慎确认。",
    external: "将向外部服务发送请求或产生外部影响；请谨慎确认。"
  }[riskLevel] ?? "该操作需要你的确认后才能继续。";
}

function toolCardStatusLabel(status: ToolCallStatus): string {
  return {
    waiting_approval: "等待确认",
    approved: "已批准",
    running: "执行中",
    completed: "已完成",
    failed: "执行失败",
    denied: "已拒绝"
  }[status];
}

function isSensitiveToolKey(key: string): boolean {
  return /(api[_-]?key|authorization|credential|password|secret|token)/i.test(key);
}

function formatToolValue(value: unknown, key = ""): string {
  if (isSensitiveToolKey(key)) return "已隐藏";
  if (typeof value === "string") {
    const compact = value.replace(/\s+/g, " ").trim();
    return compact.length > 220 ? `${compact.slice(0, 220)}...` : compact || "空";
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length === 0 ? "无" : `${value.length} 项`;
  if (value && typeof value === "object") {
    const serialized = JSON.stringify(value);
    return serialized.length > 220 ? `${serialized.slice(0, 220)}...` : serialized;
  }
  return value === null || value === undefined ? "未设置" : String(value);
}

function toolCardEntries(value: Record<string, unknown> | undefined): Array<{ key: string; value: string }> {
  if (!value) return [];
  return Object.entries(value)
    .filter(([key]) => key !== "content" && key !== "text")
    .slice(0, 8)
    .map(([key, item]) => ({ key, value: formatToolValue(item, key) }));
}

function compactToolCardEntries(value: Record<string, unknown> | undefined): Array<{ key: string; value: string }> {
  return toolCardEntries(value).slice(0, 3);
}

function toolCardScope(card: ToolCallCard): string {
  const candidate = card.args.path ?? card.args.target_path ?? card.args.title ?? card.args.query ?? card.args.subject;
  return candidate ? formatToolValue(candidate) : toolRiskDescription(card.riskLevel);
}

function upsertToolCard(
  conversationId: string,
  incoming: Omit<ToolCallCard, "id"> & { id?: string }
): ToolCallCard {
  const cards = toolCardsByConversation.value[conversationId] ?? [];
  let index = incoming.id ? cards.findIndex((card) => card.id === incoming.id) : -1;
  if (index < 0 && incoming.callId) {
    index = cards.findIndex((card) => card.callId === incoming.callId);
  }
  if (index < 0) {
    for (let cursor = cards.length - 1; cursor >= 0; cursor -= 1) {
      const candidate = cards[cursor];
      if (
        candidate.toolName === incoming.toolName &&
        (!incoming.runId || !candidate.runId || candidate.runId === incoming.runId) &&
        ["waiting_approval", "approved", "running"].includes(candidate.status)
      ) {
        index = cursor;
        break;
      }
    }
  }
  const existing = index >= 0 ? cards[index] : undefined;
  const card: ToolCallCard = {
    id: existing?.id ?? incoming.id ?? `tool-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    ...existing,
    ...incoming,
    args: Object.keys(incoming.args).length > 0 ? incoming.args : existing?.args ?? {},
    riskLevel: incoming.riskLevel || existing?.riskLevel || defaultToolRisk(incoming.toolName)
  };
  const next = index >= 0 ? cards.map((item, itemIndex) => (itemIndex === index ? card : item)) : [...cards, card];
  toolCardsByConversation.value = { ...toolCardsByConversation.value, [conversationId]: next.slice(-24) };
  return card;
}

function updatePermissionToolCard(permission: PermissionRequest, status: ToolCallStatus): void {
  upsertToolCard(permission.conversationId, {
    id: permission.toolCardId,
    runId: permission.runId,
    requestId: permission.requestId,
    toolName: permission.toolName,
    riskLevel: permission.riskLevel,
    args: toolCardsByConversation.value[permission.conversationId]?.find((card) => card.id === permission.toolCardId)?.args ?? {},
    review: toolCardsByConversation.value[permission.conversationId]?.find((card) => card.id === permission.toolCardId)?.review,
    summary: permission.summary,
    status
  });
}

function compactText(text: string, maxLength = 110): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized;
}

function reminderOutputSummary(reminder: Record<string, unknown>): string {
  if (typeof reminder.due_at === "string" && reminder.due_at) {
    const dueAt = new Date(reminder.due_at);
    const date = dueAt.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
    const time = dueAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    return `${date} ${time} · 一次性提醒`;
  }
  if (typeof reminder.time_of_day === "string" && reminder.time_of_day) {
    const schedule = reminder.schedule_type === "weekly" ? "每周" : "每天";
    return `${schedule} ${reminder.time_of_day} · 定时提醒`;
  }
  return "已写入提醒清单";
}

function taskDraftScheduleLabel(draft: TaskDraft): string {
  if (!draft.dueAt) return "未设置截止时间";
  const dueAt = new Date(draft.dueAt);
  const time = dueAt.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return draft.reminder ? `${time} 提醒` : `截止 ${time}`;
}

function taskPriorityLabel(priority: TaskDraft["priority"]): string {
  return { low: "低", normal: "普通", high: "高" }[priority];
}

function localDateTimeInput(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function syncTaskDetailDraft(task: api.TaskItem): void {
  taskDetailDraft.value = {
    title: task.title,
    description: task.description ?? "",
    dueAt: localDateTimeInput(task.due_at),
    category: task.category ?? "个人",
    priority: task.priority ?? "normal",
    nextAction: task.next_action ?? ""
  };
  taskDetailNotice.value = "";
}

function outputFromArtifact(artifact: Record<string, unknown>): ConversationOutput | null {
  const id = String(artifact.id ?? "");
  if (!id) return null;
  const rawKind = String(artifact.kind ?? "document");
  const kind: OutputKind = ["document", "research", "knowledge", "task", "file"].includes(rawKind)
    ? rawKind as OutputKind
    : "document";
  const content = String(artifact.content ?? "");
  const versionState = String(artifact.version_state ?? "draft");
  return {
    id: `artifact-${id}`,
    artifactId: id,
    kind,
    label: versionState === "draft" ? "候选成品" : undefined,
    title: String(artifact.title ?? "未命名成品"),
    summary: content ? compactText(content, 120) : "候选成品已生成，确认后会保存到工作区。",
    content,
    status: versionState === "draft" ? "review" : "ready",
    updated: "刚刚",
    version: Number(artifact.version ?? 1)
  };
}

function conversationOutputFromToolResult(
  toolName: string,
  rawResult: unknown,
  callId = ""
): ConversationOutput | null {
  const result = asRecord(rawResult);
  const data = asRecord(result?.data);
  if (!result || !data || result.status === "error") return null;

  if (toolName === "artifact_draft" || toolName === "artifact_revise") {
    return outputFromArtifact(asRecord(data.artifact) ?? {});
  }

  if (toolName === "task_draft") {
    const task = asRecord(data.task);
    if (!task) return null;
    const reminder = asRecord(data.reminder);
    const draft: TaskDraft = {
      title: String(task.title ?? "未命名待办"),
      description: String(task.description ?? "").trim(),
      dueAt: typeof task.due_at === "string" && task.due_at ? task.due_at : undefined,
      category: String(task.category ?? "个人"),
      priority: ["low", "normal", "high"].includes(String(task.priority))
        ? String(task.priority) as TaskDraft["priority"]
        : "normal",
      nextAction: String(task.next_action ?? "").trim() || undefined,
      memorySummary: String(task.memory_summary ?? "").trim() || undefined,
      reminder: reminder
        ? {
            dueAt: typeof reminder.due_at === "string" && reminder.due_at ? reminder.due_at : undefined,
            timezone: String(reminder.timezone ?? "Asia/Shanghai"),
            message: String(reminder.message ?? "").trim() || undefined
          }
        : undefined
    };
    return {
      id: `task-draft-${callId || Date.now()}`,
      kind: "task",
      label: "待办候选",
      title: draft.title,
      summary: `${taskDraftScheduleLabel(draft)} · 确认保存后加入工作区`,
      content: draft.description || draft.title,
      status: "review",
      updated: "刚刚",
      toolName,
      taskDraft: draft
    };
  }

  if (toolName === "reminder_create") {
    const reminder = asRecord(data.reminder);
    if (!reminder) return null;
    const title = String(reminder.title ?? "新提醒");
    const body = String(reminder.body ?? "").trim();
    return {
      id: `reminder-${String(reminder.id ?? Date.now())}`,
      kind: "task",
      label: "提醒",
      title,
      summary: body ? `${reminderOutputSummary(reminder)} · ${compactText(body, 72)}` : reminderOutputSummary(reminder),
      content: body || title,
      status: "ready",
      updated: "刚刚",
      toolName,
      externalRef: String(reminder.id ?? ""),
      saved: true
    };
  }

  if (toolName === "task_create" || toolName === "task_update") {
    const task = asRecord(data.task);
    if (!task) return null;
    const title = String(task.title ?? "待办");
    const dueAt = typeof task.due_at === "string" && task.due_at
      ? `截止 ${new Date(task.due_at).toLocaleString("zh-CN")}`
      : "未设置截止时间";
    const description = String(task.description ?? "").trim();
    const status = String(task.status ?? "todo");
    return {
      id: `task-${String(task.id ?? Date.now())}`,
      kind: "task",
      label: "待办",
      title,
      summary: `${dueAt}${description ? ` · ${compactText(description, 72)}` : ""}`,
      content: description || title,
      status: status === "in_progress" ? "working" : status === "done" ? "ready" : "review",
      updated: "刚刚",
      toolName,
      externalRef: String(task.id ?? ""),
      saved: true
    };
  }

  if (toolName === "task_list") {
    const taskList = Array.isArray(data.tasks) ? data.tasks : [];
    return {
      id: `task-list-${callId || Date.now()}`,
      kind: "task",
      label: "待办",
      title: "待办清单",
      summary: `当前有 ${taskList.length} 项待办`,
      content: taskList.map((item) => {
        const task = asRecord(item);
        return task ? `- ${String(task.title ?? "未命名待办")}` : "";
      }).filter(Boolean).join("\n"),
      status: "ready",
      updated: "刚刚",
      sourceCount: taskList.length,
      toolName
    };
  }

  if (toolName === "knowledge_save") {
    const title = String(data.title ?? "知识条目");
    return {
      id: `knowledge-save-${String(data.document_id ?? Date.now())}`,
      kind: "knowledge",
      label: "知识库",
      title,
      summary: "已保存到个人知识库，之后的相关提问会通过 RAG 检索。",
      content: title,
      status: "ready",
      updated: "刚刚",
      toolName,
      externalRef: String(data.document_id ?? ""),
      saved: true
    };
  }

  if (toolName === "draft_write" || toolName === "file_patch") {
    const path = String(data.draft_path ?? data.path ?? "生成文件");
    const title = path.split("/").pop() || "生成文件";
    return {
      id: `file-result-${callId || Date.now()}`,
      kind: "file",
      label: toolName === "draft_write" ? "文档" : "文件",
      title,
      summary: String(result.summary ?? "文件操作已完成"),
      content: String(data.diff ?? result.summary ?? ""),
      path,
      status: "ready",
      updated: "刚刚",
      toolName,
      saved: true
    };
  }

  if (toolName === "knowledge_search" || toolName === "knowledge_summarize") {
    const matches = Array.isArray(data.matches) ? data.matches : [];
    return {
      id: `knowledge-result-${callId || Date.now()}`,
      kind: "knowledge",
      label: "知识库",
      title: toolName === "knowledge_summarize" ? "文档总结" : "知识库检索",
      summary: String(result.summary ?? `找到 ${matches.length} 条资料`),
      content: matches.map((item) => {
        const match = asRecord(item);
        return match ? String(match.text ?? "") : "";
      }).filter(Boolean).join("\n\n"),
      status: "ready",
      updated: "刚刚",
      sourceCount: matches.length,
      toolName
    };
  }

  return null;
}

function planFromToolResult(rawResult: unknown): ConversationPlan | null {
  const result = asRecord(rawResult);
  const data = asRecord(result?.data);
  const plan = asRecord(data?.plan);
  if (!result || result.status === "error" || !plan) return null;
  const rawSteps = Array.isArray(plan.steps) ? plan.steps : [];
  const steps = rawSteps
    .map((rawStep, index): PlanStep | null => {
      const step = asRecord(rawStep);
      const title = String(step?.title ?? "").trim();
      if (!title) return null;
      return {
        id: `${index}-${title}`,
        title,
        detail: String(step?.detail ?? "").trim(),
        deliverable: String(step?.deliverable ?? "").trim() || undefined,
        status: step?.status === "done" || step?.status === "in_progress" ? step.status : "pending"
      };
    })
    .filter((step): step is PlanStep => step !== null);
  if (steps.length === 0) return null;
  const completedSteps = steps.filter((step) => step.status === "done").length;
  const hasActiveStep = steps.some((step) => step.status === "in_progress");
  return {
    title: String(plan.title ?? "执行计划").trim() || "执行计划",
    objective: String(plan.objective ?? "").trim(),
    steps,
    constraints: Array.isArray(plan.constraints)
      ? plan.constraints.map((item) => String(item).trim()).filter(Boolean).slice(0, 8)
      : [],
    successCriteria: Array.isArray(plan.success_criteria)
      ? plan.success_criteria.map((item) => String(item).trim()).filter(Boolean).slice(0, 8)
      : [],
    status: completedSteps >= steps.length ? "completed" : hasActiveStep ? "executing" : "draft",
    completedSteps
  };
}

function recordConversationPlan(conversationId: string, rawResult: unknown): void {
  const nextPlan = planFromToolResult(rawResult);
  if (!nextPlan) return;
  // 后端 plan_draft 返回的 steps status 是权威数据（_progress_update_plan 已按
  // 用户"完成前 N 步"精确计算），前端直接用，不再用 previousPlan 猜测覆盖。
  const completedSteps = nextPlan.completedSteps;
  const status = nextPlan.status;
  const plan: ConversationPlan = {
    ...nextPlan,
    status,
    completedSteps,
    steps: nextPlan.steps.map((step, index) => ({
      ...step,
      status: status === "executing" || status === "completed"
        ? index < completedSteps ? "done" : index === completedSteps && status === "executing" ? "in_progress" : "pending"
        : step.status
    }))
  };
  plansByConversation.value = {
    ...plansByConversation.value,
    [conversationId]: plan
  };
  const prefix = `${conversationId}:`;
  planStepExpanded.value = Object.fromEntries(
    Object.entries(planStepExpanded.value).filter(([key]) => !key.startsWith(prefix))
  );
  planOverviewExpanded.value = {
    ...planOverviewExpanded.value,
    [conversationId]: false
  };
}

function planStepKey(conversationId: string, step: PlanStep): string {
  return `${conversationId}:${step.id}`;
}

function isPlanStepExpanded(conversationId: string, step: PlanStep): boolean {
  return planStepExpanded.value[planStepKey(conversationId, step)] ?? false;
}

function togglePlanStep(conversationId: string, step: PlanStep): void {
  const key = planStepKey(conversationId, step);
  planStepExpanded.value = { ...planStepExpanded.value, [key]: !isPlanStepExpanded(conversationId, step) };
}

function isPlanOverviewExpanded(conversationId: string): boolean {
  return planOverviewExpanded.value[conversationId] ?? false;
}

function togglePlanOverview(conversationId: string): void {
  planOverviewExpanded.value = {
    ...planOverviewExpanded.value,
    [conversationId]: !isPlanOverviewExpanded(conversationId)
  };
}

function advancePlanProgress(conversationId: string): void {
  const plan = plansByConversation.value[conversationId];
  // 只在计划执行中且当前确有 in_progress 步骤时推进，避免计划外动作
  // （如随手问天气/闲聊）把尚未开始的 pending 步骤误标记为完成。
  if (!plan || plan.status !== "executing") return;
  const activeIndex = plan.steps.findIndex((step) => step.status === "in_progress");
  if (activeIndex < 0) return;
  const completedSteps = Math.min(plan.steps.length, activeIndex + 1);
  plansByConversation.value = {
    ...plansByConversation.value,
    [conversationId]: {
      ...plan,
      completedSteps,
      status: completedSteps >= plan.steps.length ? "completed" : "executing",
      steps: plan.steps.map((step, index) => ({
        ...step,
        status: index < completedSteps ? "done" : index === completedSteps ? "in_progress" : "pending"
      }))
    }
  };
}

function upsertConversationOutput(conversationId: string, output: ConversationOutput): void {
  const current = conversationOutputsByConversation.value[conversationId] ?? [];
  const next = [output, ...current.filter((item) => item.id !== output.id)];
  conversationOutputsByConversation.value = {
    ...conversationOutputsByConversation.value,
    [conversationId]: next.slice(0, 8)
  };
}

function upsertTaskDraft(conversationId: string, output: ConversationOutput): void {
  if (!output.taskDraft) return;
  const current = pendingTaskDraftsByConversation.value[conversationId] ?? [];
  const sameDraft = (item: ConversationOutput) =>
    item.taskDraft?.title === output.taskDraft?.title &&
    item.taskDraft?.dueAt === output.taskDraft?.dueAt;
  pendingTaskDraftsByConversation.value = {
    ...pendingTaskDraftsByConversation.value,
    [conversationId]: [output, ...current.filter((item) => item.id !== output.id && !sameDraft(item))].slice(0, 4)
  };
}

function removeTaskDraft(conversationId: string, output: ConversationOutput): void {
  const current = pendingTaskDraftsByConversation.value[conversationId] ?? [];
  pendingTaskDraftsByConversation.value = {
    ...pendingTaskDraftsByConversation.value,
    [conversationId]: current.filter((item) => item.id !== output.id)
  };
}

function recordConversationOutput(conversationId: string, output: ConversationOutput): void {
  if (output.taskDraft && !output.saved) {
    const alreadyConfirmed = tasks.value.some(
      (task) => task.source_session_id === conversationId &&
        task.title === output.taskDraft?.title &&
        task.due_at === output.taskDraft?.dueAt
    );
    if (alreadyConfirmed) return;
    upsertTaskDraft(conversationId, output);
    return;
  }
  // Search, summaries, task lists, and draft artifacts stay in the message
  // stream until the user explicitly saves them to the workspace.
  if (!output.saved) return;
  upsertConversationOutput(conversationId, output);
}

function restoreConversationOutputs(
  conversationId: string,
  sessionMessages: api.SessionMessage[]
): void {
  conversationOutputsByConversation.value = {
    ...conversationOutputsByConversation.value,
    [conversationId]: []
  };
  const remainingPlans = { ...plansByConversation.value };
  delete remainingPlans[conversationId];
  plansByConversation.value = remainingPlans;
  pendingTaskDraftsByConversation.value = {
    ...pendingTaskDraftsByConversation.value,
    [conversationId]: []
  };
  const restoredToolCards: ToolCallCard[] = [];
  let latestCitations: Citation[] = [];
  for (const message of sessionMessages) {
    if (message.role !== "assistant") continue;
    const metadata = message.metadata ?? {};
    const rawCitations = Array.isArray(metadata.citations) ? metadata.citations : [];
    if (rawCitations.length > 0) {
      latestCitations = rawCitations
        .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
        .map<Citation>((item) => ({
          title: String(item.title ?? "知识库"),
          source_path: String(item.source_path ?? ""),
          source_type: item.source_type === "web" ? "web" : "knowledge",
          url: String(item.url ?? "") || undefined,
          document_id: String(item.document_id ?? "") || undefined,
          chunk_id: String(item.chunk_id ?? "")
        }));
    }
    const rawCalls = Array.isArray(metadata.tool_calls) ? metadata.tool_calls : [];
    const rawResults = Array.isArray(metadata.tool_results) ? metadata.tool_results : [];
    rawCalls.forEach((rawCall, index) => {
      const call = asRecord(rawCall);
      if (!call) return;
      const toolName = String(call.name ?? "tool");
      const result = asRecord(rawResults[index]);
      const resultStatus = String(result?.status ?? "");
      restoredToolCards.push({
        id: `tool-history-${message.id}-${index}`,
        callId: String(call.id ?? "") || undefined,
        runId: messageRunId(metadata),
        toolName,
        riskLevel: String(call.risk_level ?? defaultToolRisk(toolName)),
        args: asRecord(call.args) ?? {},
        result: result ?? undefined,
        summary: result ? String(result.summary ?? "工具已执行") : undefined,
        status: result
          ? resultStatus === "error"
            ? "failed"
            : "completed"
          : metadata.run_status === "permission_denied"
            ? "denied"
            : "failed"
      });
    });
    rawResults.forEach((rawResult, index) => {
      const call = asRecord(rawCalls[index] ?? rawCalls[0]);
      if (String(call?.name ?? "") === "plan_draft") {
        recordConversationPlan(conversationId, rawResult);
      }
      const output = conversationOutputFromToolResult(String(call?.name ?? ""), rawResult);
      if (output) {
        recordConversationOutput(conversationId, output);
      }
    });
  }
  citationsByConversation.value = {
    ...citationsByConversation.value,
    [conversationId]: latestCitations
  };
  toolCardsByConversation.value = {
    ...toolCardsByConversation.value,
    [conversationId]: restoredToolCards.slice(-24)
  };
}

function outputStatusLabel(status: ConversationOutput["status"], saved = false): string {
  if (saved) return "已保存";
  return { ready: "已完成", review: "待确认", working: "处理中" }[status];
}

function taskStatusLabel(status: api.TaskItem["status"] | ConversationOutput["status"]): string {
  return {
    todo: "待办",
    in_progress: "进行中",
    done: "已完成",
    ready: "已完成",
    review: "待确认",
    working: "进行中"
  }[status];
}

async function openOutput(output: ConversationOutput): Promise<void> {
  if (output.kind === "file" && output.path) {
    openFilePreview(output.path);
    return;
  }
  if (output.externalRef) {
    const workspaceItem = workspaceLibraryItems.value.find(
      (item) => item.externalRef === output.externalRef && item.kind === output.kind
    );
    if (workspaceItem) {
      await openWorkspaceItem(workspaceItem);
      return;
    }
  }
  activeArtifact.value = output;
  activeWorkspaceItem.value = null;
  activeView.value = "chat";
}

function continueArtifactRevision(): void {
  const artifact = activeArtifact.value;
  if (!artifact?.artifactId) return;
  pendingArtifactRevisionId.value = artifact.artifactId;
  activeArtifact.value = null;
  composerText.value = "";
  activeView.value = "chat";
}

function requestSaveArtifact(): void {
  if (!activeArtifact.value || (!activeArtifact.value.artifactId && !activeArtifact.value.taskDraft)) return;
  saveConfirmationOpen.value = true;
}

async function confirmSaveArtifact(): Promise<void> {
  if (!activeArtifact.value) return;
  const output = activeArtifact.value;
  if (output.taskDraft && hasBackendToken.value) {
    try {
      const sessionId = activeConversationId.value;
      if (!sessionId) {
        throw new Error("请先在对话中生成待办候选。");
      }
      const confirmed = await api.confirmTaskDraft({
        session_id: sessionId,
        title: output.taskDraft.title,
        description: output.taskDraft.description || undefined,
        due_at: output.taskDraft.dueAt,
        timezone: output.taskDraft.reminder?.timezone ?? "Asia/Shanghai",
        create_reminder: Boolean(output.taskDraft.reminder?.dueAt),
        category: output.taskDraft.category,
        priority: output.taskDraft.priority,
        next_action: output.taskDraft.nextAction,
        reminder_message: output.taskDraft.reminder?.message,
        memory_summary: output.taskDraft.memorySummary
      });
      const task = confirmed.task;
      const reminder = confirmed.reminder ?? undefined;
      tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)];
      if (reminder) {
        reminders.value = [reminder, ...reminders.value.filter((item) => item.id !== reminder!.id)];
      }
      const savedOutput: ConversationOutput = {
        ...output,
        label: "待办",
        summary: taskDraftScheduleLabel(output.taskDraft),
        externalRef: task.id,
        reminderId: reminder?.id,
        saved: true
      };
      const conversationId = activeConversationId.value ?? "";
      removeTaskDraft(conversationId, output);
      upsertConversationOutput(conversationId, savedOutput);
      activeArtifact.value = null;
      activeView.value = "chat";
      workspaceSaveNotice.value = `“${output.title}”已保存为待办事项`;
      saveConfirmationOpen.value = false;
      return;
    } catch (error) {
      backendError.value = error instanceof Error ? error.message : String(error);
      return;
    }
  }
  if (!hasBackendToken.value) {
    const alreadySaved = workspaceLibraryItems.value.some((item) => item.title === output.title);
    if (!alreadySaved) {
      workspaceItems.value.unshift({
        ...output,
        id: `workspace-${Date.now()}`,
        savedAt: "刚刚",
        conversationId: activeConversationId.value ?? undefined
      });
    }
    if (output.taskDraft) {
      const conversationId = activeConversationId.value ?? "";
      const savedOutput: ConversationOutput = {
        ...output,
        label: "待办",
        summary: taskDraftScheduleLabel(output.taskDraft),
        saved: true
      };
      removeTaskDraft(conversationId, output);
      upsertConversationOutput(conversationId, savedOutput);
      activeArtifact.value = null;
      activeView.value = "chat";
    } else {
      const conversationId = activeConversationId.value ?? "";
      upsertConversationOutput(conversationId, { ...output, saved: true, status: "ready" });
      activeArtifact.value = null;
    }
  } else {
    try {
      const draft = output.artifactId
        ? await api.confirmArtifact(output.artifactId)
        : await api.createArtifact({
            kind: output.kind,
            title: output.title,
            content: output.content ?? output.summary,
            source_session_id: activeConversationId.value ?? undefined,
            external_ref: output.externalRef,
            metadata: { tool_name: output.toolName ?? "conversation" }
          });
      const saved = draft.state === "saved" ? draft : await api.confirmArtifact(draft.id);
      artifacts.value = [saved, ...artifacts.value.filter((item) => item.id !== saved.id)];
      const savedOutput = outputFromArtifact(asRecord(saved) ?? {});
      if (savedOutput) {
        const conversationId = activeConversationId.value ?? "";
        const mergedOutput: ConversationOutput = {
          ...output,
          ...savedOutput,
          status: "ready",
          saved: true
        };
        upsertConversationOutput(conversationId, mergedOutput);
        activeArtifact.value = mergedOutput;
      } else {
        activeArtifact.value = output;
      }
    } catch (error) {
      backendError.value = error instanceof Error ? error.message : String(error);
      return;
    }
  }
  workspaceSaveNotice.value = `“${output.title}”已保存到工作区`;
  saveConfirmationOpen.value = false;
}

async function saveTaskDraftFromOutput(output: ConversationOutput): Promise<void> {
  if (!output.taskDraft || output.saved) return;
  activeArtifact.value = output;
  await confirmSaveArtifact();
}

function requestTaskDraftConfirmation(output: ConversationOutput): void {
  activeArtifact.value = output;
  saveConfirmationOpen.value = true;
}

function dismissTaskDraft(output: ConversationOutput): void {
  const conversationId = activeConversationId.value ?? "";
  removeTaskDraft(conversationId, output);
  if (activeArtifact.value?.id === output.id) activeArtifact.value = null;
}

function fsRowIsDirectory(row: FsTreeRow): boolean {
  return row.type === "root" || row.type === "dir";
}

async function loadFsRoots(): Promise<void> {
  fsRootsLoading.value = true;
  fsContextError.value = "";
  try {
    const { roots } = await api.fetchFsRoots();
    fsRoots.value = roots;
    const firstExisting = roots.find((root) => root.exists);
    if (!fsSelectedPath.value && firstExisting) {
      fsSelectedPath.value = firstExisting.path;
      fsExpandedPaths.value = { ...fsExpandedPaths.value, [firstExisting.path]: true };
      await loadFsTree(firstExisting.path);
    }
  } catch (error) {
    fsContextError.value = error instanceof Error ? error.message : "无法读取可访问目录";
  } finally {
    fsRootsLoading.value = false;
  }
}

async function loadFsTree(path: string): Promise<void> {
  if (fsTreeByPath.value[path]) return;
  fsTreeLoadingPath.value = path;
  fsContextError.value = "";
  try {
    const tree = await api.fetchFsTree(path);
    fsTreeByPath.value = { ...fsTreeByPath.value, [path]: tree };
  } catch (error) {
    fsContextError.value = error instanceof Error ? error.message : "无法读取目录树";
  } finally {
    fsTreeLoadingPath.value = "";
  }
}

async function toggleFsDirectory(row: FsTreeRow): Promise<void> {
  if (!fsRowIsDirectory(row) || row.exists === false) return;
  const willExpand = !fsExpandedPaths.value[row.path];
  fsExpandedPaths.value = { ...fsExpandedPaths.value, [row.path]: willExpand };
  fsSelectedPath.value = row.path;
  if (willExpand) {
    await loadFsTree(row.path);
  }
}

async function selectFsDirectory(row: FsTreeRow): Promise<void> {
  if (!fsRowIsDirectory(row) || row.exists === false) return;
  fsSelectedPath.value = row.path;
  if (!fsTreeByPath.value[row.path]) {
    await loadFsTree(row.path);
  }
}

function fsContextPayload(context: api.FsContext): WorkspaceContextPayload {
  const extensionSummary =
    context.extensions.length > 0
      ? context.extensions.map((item) => `${item.ext} × ${item.count}`).join("、")
      : "暂无扩展名统计";
  const content = [
    `项目名称：${context.name}`,
    `项目路径：${context.path}`,
    `文件数量：${context.total_files}`,
    `主要类型：${extensionSummary}`,
    "",
    "目录结构：",
    context.tree || "（没有可展示的目录结构）",
    "",
    "README：",
    context.readme || "（未找到 README 或 README 为空）"
  ].join("\n");
  return {
    title: context.name,
    kind: "directory",
    content: content.slice(0, 20_000)
  };
}

async function generateFsContext(path = fsSelectedPath.value): Promise<void> {
  if (!path) {
    fsContextError.value = "请先选择一个项目文件夹";
    return;
  }
  fsContextLoading.value = true;
  fsContextError.value = "";
  try {
    const context = await api.fetchFsContext(path);
    fsContext.value = context;
    pendingWorkspaceContext.value = fsContextPayload(context);
    workspaceSaveNotice.value = `已生成“${context.name}”项目概览，并加入下一条消息上下文`;
    if (!composerText.value.trim()) {
      composerText.value = `请基于项目“${context.name}”的结构和 README 帮我分析：`;
      resizeActiveComposers();
    }
  } catch (error) {
    fsContextError.value = error instanceof Error ? error.message : "生成项目上下文失败";
  } finally {
    fsContextLoading.value = false;
  }
}

async function toggleProjectContextPanel(): Promise<void> {
  projectContextOpen.value = !projectContextOpen.value;
  if (projectContextOpen.value && fsRoots.value.length === 0) {
    await loadFsRoots();
  }
}

function askWithProjectContext(): void {
  activeView.value = "chat";
  workspaceSaveNotice.value = "";
}

function projectPathLabel(path: string): string {
  const normalized = path.replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized || path;
  return `…/${parts.slice(-2).join("/")}`;
}

function projectDirectoryName(project: api.ProjectRecord): string {
  return project.path.replace(/\/+$/, "").split("/").filter(Boolean).pop() || project.name;
}

function projectSessions(projectId: string): api.SessionRecord[] {
  return projectSessionsByProject.value[projectId] ?? [];
}

function conversationFromSession(session: api.SessionRecord, activeId = activeConversationId.value): Conversation {
  return {
    id: session.id,
    title: session.title,
    date: displayDate(session.updated_at),
    status: "done",
    mode: session.mode,
    projectId: session.project_id ?? null,
    active: session.id === activeId
  };
}

function upsertConversationFromSession(session: api.SessionRecord): void {
  const conversation = conversationFromSession(session);
  const existing = conversations.value.findIndex((item) => item.id === session.id);
  if (existing >= 0) {
    conversations.value = conversations.value.map((item) =>
      item.id === session.id ? { ...item, ...conversation } : item
    );
    return;
  }
  conversations.value = [conversation, ...conversations.value.map((item) => ({ ...item, active: false }))];
}

async function loadProjects(): Promise<void> {
  if (!hasBackendToken.value) return;
  try {
    const { projects: items } = await api.fetchProjects();
    projects.value = items;
    for (const project of items.filter((item) => expandedProjectIds.value[item.id])) {
      void loadProjectSessions(project.id);
    }
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "无法读取项目列表";
  }
}

async function loadProjectSessions(projectId: string): Promise<void> {
  if (!hasBackendToken.value) return;
  projectSessionsLoading.value = { ...projectSessionsLoading.value, [projectId]: true };
  try {
    const { sessions } = await api.fetchProjectSessions(projectId);
    projectSessionsByProject.value = { ...projectSessionsByProject.value, [projectId]: sessions };
    for (const session of sessions) {
      upsertConversationFromSession(session);
    }
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "无法读取项目对话";
  } finally {
    projectSessionsLoading.value = { ...projectSessionsLoading.value, [projectId]: false };
  }
}

async function openAddProjectDialog(): Promise<void> {
  projectDialogOpen.value = true;
  projectError.value = "";
  projectDraftName.value = "";
  projectDraftPath.value = "";
  await chooseProjectDirectory();
}

async function chooseProjectDirectory(): Promise<void> {
  projectError.value = "";
  if (!window.desktopMate?.selectDirectory) {
    projectError.value = "当前环境不支持系统目录选择器，请手动输入项目路径";
    return;
  }
  try {
    const selected = await window.desktopMate.selectDirectory();
    if (!selected) return;
    projectDraftPath.value = selected.path;
    projectDraftName.value = selected.name;
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "打开 Finder 选择目录失败";
  }
}

async function submitAddProject(): Promise<void> {
  const path = projectDraftPath.value.trim();
  const fallbackName = path.replace(/\/+$/, "").split("/").filter(Boolean).pop() ?? "未命名项目";
  const name = projectDraftName.value.trim() || fallbackName;
  if (!path) {
    projectError.value = "请输入项目目录路径";
    return;
  }
  projectAdding.value = true;
  projectError.value = "";
  try {
    const { project } = await api.addProject({ name, path });
    projects.value = [project, ...projects.value.filter((item) => item.id !== project.id)];
    expandedProjectIds.value = { ...expandedProjectIds.value, [project.id]: true };
    activeProjectId.value = project.id;
    projectDialogOpen.value = false;
    await Promise.all([loadProjectSessions(project.id), loadProjectRoot(project)]);
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "添加项目失败";
  } finally {
    projectAdding.value = false;
  }
}

async function removeProject(project: api.ProjectRecord): Promise<void> {
  if (!window.confirm(`从 DeskBits 移除项目“${project.name}”？仅移除引用，不删除磁盘文件。`)) return;
  try {
    await api.deleteProject(project.id);
    projects.value = projects.value.filter((item) => item.id !== project.id);
    const { [project.id]: _sessions, ...nextSessions } = projectSessionsByProject.value;
    projectSessionsByProject.value = nextSessions;
    if (activeProjectId.value === project.id) {
      activeProjectId.value = null;
      activeProjectFile.value = null;
      if (activeView.value === "project") activeView.value = "chat";
    }
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "移除项目失败";
  }
}

async function toggleProject(project: api.ProjectRecord): Promise<void> {
  const nextExpanded = !expandedProjectIds.value[project.id];
  expandedProjectIds.value = { ...expandedProjectIds.value, [project.id]: nextExpanded };
  activeProjectId.value = project.id;
  activeView.value = "project";
  if (nextExpanded) {
    await Promise.all([loadProjectSessions(project.id), loadProjectRoot(project)]);
  }
}

async function openProjectFiles(project: api.ProjectRecord): Promise<void> {
  activeProjectId.value = project.id;
  expandedProjectIds.value = { ...expandedProjectIds.value, [project.id]: true };
  activeView.value = "project";
  if (activeProjectFilePath.value && !activeProjectFilePath.value.startsWith(project.path.replace(/\/+$/, ""))) {
    activeProjectFilePath.value = "";
    projectFileNotice.value = "";
    activeProjectFile.value = null;
  }
  await loadProjectRoot(project);
}

async function refreshProjectFiles(project: api.ProjectRecord): Promise<void> {
  const nextTree = { ...projectFileTree.value };
  for (const path of Object.keys(nextTree)) {
    if (path === project.path || path.startsWith(`${project.path.replace(/\/+$/, "")}/`)) {
      delete nextTree[path];
    }
  }
  projectFileTree.value = nextTree;
  const nextCache = { ...projectContextCache.value };
  delete nextCache[project.id];
  projectContextCache.value = nextCache;
  await openProjectFiles(project);
}

async function openProjectSession(project: api.ProjectRecord, session: api.SessionRecord): Promise<void> {
  activeProjectId.value = project.id;
  expandedProjectIds.value = { ...expandedProjectIds.value, [project.id]: true };
  upsertConversationFromSession(session);
  await selectConversation(session.id);
  activeProjectId.value = project.id;
  activeView.value = "project";
  await loadProjectRoot(project);
}

async function startProjectSession(project: api.ProjectRecord): Promise<void> {
  activeProjectId.value = project.id;
  expandedProjectIds.value = { ...expandedProjectIds.value, [project.id]: true };
  try {
    const { session } = await api.createProjectSession(project.id);
    projectSessionsByProject.value = {
      ...projectSessionsByProject.value,
      [project.id]: [session, ...projectSessions(project.id).filter((item) => item.id !== session.id)]
    };
    upsertConversationFromSession(session);
    await selectConversation(session.id);
    activeProjectId.value = project.id;
    activeView.value = "project";
    await loadProjectRoot(project);
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : "新建项目对话失败";
  }
}

async function loadProjectRoot(project: api.ProjectRecord): Promise<void> {
  if (!project.exists) return;
  projectExpandedPaths.value = { ...projectExpandedPaths.value, [project.path]: true };
  await loadProjectTree(project.path);
}

async function loadProjectTree(path: string): Promise<void> {
  if (projectFileTree.value[path]) return;
  projectFileLoadingPath.value = path;
  projectFileError.value = "";
  try {
    const tree = await api.fetchFsTree(path);
    projectFileTree.value = { ...projectFileTree.value, [path]: tree };
  } catch (error) {
    projectFileError.value = error instanceof Error ? error.message : "无法读取项目文件树";
  } finally {
    projectFileLoadingPath.value = "";
  }
}

async function toggleProjectDirectory(row: FsTreeRow): Promise<void> {
  if (!fsRowIsDirectory(row) || row.exists === false) return;
  const willExpand = !projectExpandedPaths.value[row.path];
  projectExpandedPaths.value = { ...projectExpandedPaths.value, [row.path]: willExpand };
  if (willExpand) {
    await loadProjectTree(row.path);
  }
}

async function openProjectFile(row: FsTreeRow): Promise<void> {
  if (row.type !== "file") return;
  activeProjectFilePath.value = row.path;
  activeProjectFile.value = null;
  projectFileDraft.value = "";
  projectFileEditMode.value = false;
  projectFileError.value = "";
  projectFileNotice.value = `已选中 ${row.name}，预览待上线`;
}

function cancelProjectFileEdit(): void {
  projectFileDraft.value = activeProjectFile.value?.content ?? "";
  projectFileEditMode.value = false;
  projectFileNotice.value = "";
}

async function saveProjectFile(): Promise<void> {
  if (!activeProjectFile.value) return;
  projectFileSaving.value = true;
  projectFileError.value = "";
  projectFileNotice.value = "";
  try {
    const result = await api.writeFsFile({
      path: activeProjectFile.value.path,
      content: projectFileDraft.value
    });
    activeProjectFile.value = {
      ...activeProjectFile.value,
      content: projectFileDraft.value,
      size: result.size
    };
    if (activeProject.value) {
      const nextCache = { ...projectContextCache.value };
      delete nextCache[activeProject.value.id];
      projectContextCache.value = nextCache;
    }
    projectFileEditMode.value = false;
    projectFileNotice.value = "文件已保存";
  } catch (error) {
    projectFileError.value = error instanceof Error ? error.message : "保存失败";
  } finally {
    projectFileSaving.value = false;
  }
}

function projectFileIconClass(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["ts", "tsx", "js", "jsx", "vue"].includes(ext)) return "code";
  if (["py", "rb", "go", "rs", "java", "kt", "swift"].includes(ext)) return "code alt";
  if (["md", "markdown", "txt"].includes(ext)) return "doc";
  if (["json", "yaml", "yml", "toml"].includes(ext)) return "data";
  return "file";
}

async function projectWorkspaceContextFor(project: api.ProjectRecord): Promise<WorkspaceContextPayload> {
  const cached = projectContextCache.value[project.id];
  if (cached) return cached;
  const context = await api.fetchFsContext(project.path);
  const payload = fsContextPayload(context);
  projectContextCache.value = { ...projectContextCache.value, [project.id]: payload };
  return payload;
}

function mergeWorkspaceContexts(
  projectContext: WorkspaceContextPayload | null,
  manualContext: WorkspaceContextPayload | null
): WorkspaceContextPayload | undefined {
  if (projectContext && manualContext) {
    return {
      title: `${projectContext.title} + ${manualContext.title}`,
      kind: "directory",
      content: [
        "[项目上下文]",
        projectContext.content,
        "",
        "[手动添加的参考资料]",
        `标题：${manualContext.title}`,
        `类型：${manualContext.kind}`,
        manualContext.content
      ].join("\n").slice(0, 24_000)
    };
  }
  return projectContext ?? manualContext ?? undefined;
}

function openConversationPicker(item: WorkspaceItem): void {
  pendingWorkspaceItem.value = item;
  conversationPickerOpen.value = true;
}

function openWorkspace(): void {
  activeView.value = "workspace";
  workspaceCategory.value = "all";
  workspaceSearch.value = "";
}

async function openWorkspaceItem(item: WorkspaceItem): Promise<void> {
  // 文件类成果直接打开预览（doc/ppt/md 等）
  if (item.kind === "file" && item.path) {
    openFilePreview(item.path);
    return;
  }
  // 打开前补齐真实内容：知识文档 → 原文件预览；会话 → 消息文本
  let resolved = item;
  if (item.artifactId && hasBackendToken.value) {
    try {
      const artifact = await api.fetchArtifact(item.artifactId);
      resolved = {
        ...item,
        ...(outputFromArtifact(asRecord(artifact) ?? {}) ?? {}),
        id: item.id,
        savedAt: item.savedAt,
        conversationId: artifact.source_session_id ?? item.conversationId,
        externalRef: artifact.external_ref ?? item.externalRef
      };
    } catch (error) {
      backendError.value = error instanceof Error ? error.message : String(error);
    }
  } else if (item.kind === "knowledge" && item.path) {
    try {
      const preview = await api.previewFile(item.path);
      resolved = {
        ...item,
        content: preview.content,
        summary: `${preview.name} · ${previewKindLabel(preview.kind)} · ${formatBytes(preview.size)}`
      };
    } catch {
      // 原文件不可读时保留条目信息
    }
  } else if (item.kind === "document" && item.conversationId) {
    try {
      const { messages } = await api.fetchSessionMessages(item.conversationId);
      const lines = (messages ?? [])
        .filter((m) => m.role !== "system")
        .slice(-20)
        .map((m) => `${m.role === "user" ? "我" : "DeskBits"}：${m.text}`)
        .join("\n\n");
      if (lines.trim()) {
        resolved = { ...item, content: lines };
      }
    } catch {
      // 会话不可读时保留条目信息
    }
  }
  activeArtifact.value = null;
  activeWorkspaceItem.value = resolved;
  workspaceEditText.value = "";
  const task = resolved.kind === "task" ? workspaceTask(resolved) : undefined;
  if (task) syncTaskDetailDraft(task);
  activeView.value = "artifact";
}

const activeWorkspaceEditMessages = computed(() =>
  activeWorkspaceItem.value ? workspaceEditMessages.value[activeWorkspaceItem.value.id] ?? [] : []
);

const activeWorkspaceTask = computed(() =>
  activeWorkspaceItem.value ? workspaceTask(activeWorkspaceItem.value) : undefined
);

const activeWorkspaceReminder = computed(() =>
  activeWorkspaceItem.value ? workspaceReminder(activeWorkspaceItem.value) : undefined
);

function workspaceDocumentTitle(item: WorkspaceItem): string {
  return item.title;
}

function workspaceTask(item: WorkspaceItem): api.TaskItem | undefined {
  return item.externalRef ? tasks.value.find((task) => task.id === item.externalRef) : undefined;
}

function workspaceReminder(item: WorkspaceItem): api.ReminderItem | undefined {
  return item.reminderId ? reminders.value.find((reminder) => reminder.id === item.reminderId) : undefined;
}

function taskDueLabel(dueAt?: string): string {
  if (!dueAt) return "未安排日期";
  const due = new Date(dueAt);
  if (Number.isNaN(due.getTime())) return dueAt;
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dueStart = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
  const dayOffset = Math.round((dueStart - start) / 86_400_000);
  const prefix = dayOffset === 0 ? "今天" : dayOffset === 1 ? "明天" : dayOffset === -1 ? "昨天" : due.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", weekday: "short" });
  return `${prefix} ${due.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

function taskDraftMainText(draft: TaskDraft): string {
  return draft.nextAction || draft.description || "确认后会加入你的待办清单。";
}

async function workspaceContextFor(item: WorkspaceItem): Promise<WorkspaceContextPayload> {
  let content = item.content?.trim() || item.summary;
  if (item.artifactId && hasBackendToken.value) {
    try {
      const artifact = await api.fetchArtifact(item.artifactId);
      content = artifact.content || content;
    } catch {
      // 网络不可用时仍可用工作区卡片已有摘要继续。
    }
  } else if (item.kind === "task") {
    const task = workspaceTask(item);
    const reminder = workspaceReminder(item);
    if (task) {
      content = [
        `标题：${task.title}`,
        `说明：${task.description || "无"}`,
        `状态：${taskStatusLabel(task.status)}`,
        `截止：${task.due_at || "未设置"}`
      ].join("\n");
    } else if (reminder) {
      content = [
        `标题：${reminder.title}`,
        `说明：${reminder.body || "无"}`,
        `日程：${reminderScheduleLabel(reminder)}`,
        `状态：${reminder.status === "active" ? "已启用" : "已完成"}`
      ].join("\n");
    }
  } else if (item.kind === "knowledge" && item.path && hasBackendToken.value) {
    try {
      content = (await api.previewFile(item.path)).content || content;
    } catch {
      // 无法读取原文件时，摘要仍足以告诉用户带入了哪份资料。
    }
  }
  return {
    title: item.title,
    kind: outputKindLabel(item.kind, item.label),
    content: content.slice(0, 20_000) || "这个工作区条目暂时没有可读取的正文。"
  };
}

function artifactContentBlocks(content?: string, fallback = ""): ArtifactContentBlock[] {
  const source = (content || fallback || "这个成果没有可显示的内容。").slice(0, 20000).trim();
  const blocks: ArtifactContentBlock[] = [];
  let paragraph: string[] = [];
  let listType: "bullet_list" | "ordered_list" | null = null;
  let listItems: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length > 0) blocks.push({ type: "paragraph", text: paragraph.join("\n") });
    paragraph = [];
  };
  const flushList = () => {
    if (listType && listItems.length > 0) blocks.push({ type: listType, items: listItems });
    listType = null;
    listItems = [];
  };

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(?:#{1,3}\s+|(?:[一二三四五六七八九十]+、|\d+[.、])\s*)(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ type: "heading", text: heading[1] });
      continue;
    }
    const bullet = line.match(/^[-*+]\s+(.+)$/);
    const ordered = line.match(/^\d+[.)、]\s+(.+)$/);
    if (bullet || ordered) {
      const nextType = bullet ? "bullet_list" : "ordered_list";
      if (listType && listType !== nextType) flushList();
      flushParagraph();
      listType = nextType;
      listItems.push((bullet ?? ordered)![1]);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return blocks;
}

async function sendWorkspaceEdit(): Promise<void> {
  const item = activeWorkspaceItem.value;
  const text = workspaceEditText.value.trim() || "请根据附图修改这份成品。";
  if (!item || !text) return;
  if (pendingDocumentUploading.value) {
    showAttachmentToast("附件仍在上传中，请稍候发送");
    return;
  }
  let attachments: api.AttachmentItem[] = [];
  if (pendingImages.value.length > 0 && hasBackendToken.value) {
    try {
      const uploaded = await api.uploadImages(pendingImages.value.map((image) => image.file));
      attachments = [...attachments, ...uploaded.attachments];
    } catch (error) {
      imageUploadError.value = error instanceof Error ? error.message : String(error);
      return;
    }
  }
  attachments = [...attachments, ...readyPendingAttachments()];
  let conversationId = item.conversationId;
  if (!conversationId) {
    conversationId = createLocalConversation(`修改：${item.title}`);
    item.conversationId = conversationId;
  }
  const now = Date.now();
  const userMessage: WorkspaceEditMessage = { id: `workspace-user-${now}`, role: "user", text, attachments };
  workspaceEditMessages.value = {
    ...workspaceEditMessages.value,
    [item.id]: [...activeWorkspaceEditMessages.value, userMessage]
  };
  const previousMessages = messagesByConversation.value[conversationId] ?? [];
  messagesByConversation.value = {
    ...messagesByConversation.value,
    [conversationId]: [
      ...previousMessages,
      { id: `workspace-source-${now}`, role: "system", text: `已从工作区打开：${item.title}` },
      { id: `workspace-user-chat-${now}`, role: "user", text, attachments }
    ]
  };
  workspaceEditText.value = "";
  pendingImages.value = [];
  pendingAttachments.value = [];
  if (hasBackendToken.value && connectionStatus.value === "connected") {
    void sendToAgent(text, conversationId, attachments, item.artifactId);
    return;
  }
  appendWorkspaceSystemMessage("需要连接本地 Agent 服务才能生成候选修改版本。");
}

async function addWorkspaceItemToConversation(targetId: string): Promise<void> {
  const item = pendingWorkspaceItem.value;
  if (!item) return;
  pendingWorkspaceContext.value = await workspaceContextFor(item);
  if (targetId === "new") {
    await startNewChat();
  } else {
    await selectConversation(targetId);
  }
  composerText.value = `请继续处理工作区内容“${item.title}”：`;
  if (activeConversationId.value) {
    appendMessage(activeConversationId.value, "system", `已添加工作区参考资料：${item.title}`);
  }
  conversationPickerOpen.value = false;
  pendingWorkspaceItem.value = null;
  workspaceSaveNotice.value = `已将“${item.title}”添加到当前对话`;
}

function citationDocumentId(citation: Citation): string | undefined {
  if (citation.document_id) return citation.document_id;
  const candidates = [citation.title, citation.source_path.split("/").pop() ?? ""];
  return candidates.find((value) => value.startsWith("doc_"));
}

function citationDocument(citation: Citation): api.KnowledgeDocument | undefined {
  const documentId = citationDocumentId(citation);
  return documentId ? documents.value.find((document) => document.id === documentId) : undefined;
}

function citationDisplayName(citation: Citation): string {
  if (citation.source_type === "web") return citation.title || citation.url || "网页来源";
  const document = citationDocument(citation);
  if (document?.name) return document.name;
  if (citation.title && !citation.title.startsWith("doc_")) return citation.title;
  const base = citation.source_path.split("/").pop() || "";
  return base && !base.startsWith("doc_") ? base : "知识库资料";
}

async function openCitationSource(citation: Citation): Promise<void> {
  if (citation.source_type === "web" && citation.url) {
    window.open(citation.url, "_blank", "noopener,noreferrer");
    return;
  }
  const documentId = citationDocumentId(citation);
  let displayName = citationDisplayName(citation);
  if (documentId && hasBackendToken.value) {
    try {
      const document = await api.fetchDocument(documentId);
      documents.value = [document, ...documents.value.filter((item) => item.id !== document.id)];
      displayName = document.name;
    } catch {
      // The citation remains usable as a filename search when the document was deleted.
    }
  }
  knowledgeSearch.value = displayName;
  knowledgeFilter.value = "all";
  activeView.value = "knowledge";
}

const activeAttachmentCount = computed(() =>
  chatMessages.value.reduce((count, message) => count + (message.attachments?.length ?? 0), 0)
);

const currentConversation = computed(() =>
  conversations.value.find((conversation) => conversation.id === activeConversationId.value)
);

const currentTitle = computed(() => {
  if (activeView.value === "today") {
    return "今日";
  }
  if (activeView.value === "files") {
    return "文件";
  }
  if (activeView.value === "tasks") {
    return "任务";
  }
  if (activeView.value === "knowledge") {
    return "知识库";
  }
  if (activeView.value === "workspace") {
    return "工作区";
  }
  if (activeView.value === "project") {
    return activeProject.value?.name ?? "项目";
  }
  if (activeView.value === "artifact") {
    return activeWorkspaceItem.value?.title ?? "工作区成品";
  }
  if (activeView.value === "settings") {
    return "Settings";
  }
  return currentConversation.value?.title ?? "对话";
});

const filteredConversations = computed(() => {
  const query = conversationSearch.value.trim().toLowerCase();
  if (!query) {
    return conversations.value;
  }
  return conversations.value.filter((conversation) =>
    conversation.title.toLowerCase().includes(query)
  );
});

const groupedConversations = computed<ConversationGroup[]>(() => {
  return filteredConversations.value.reduce<ConversationGroup[]>((groups, conversation) => {
    const group = groups.find((item) => item.date === conversation.date);
    if (group) {
      group.conversations.push(conversation);
    } else {
      groups.push({ date: conversation.date, conversations: [conversation] });
    }
    return groups;
  }, []);
});

const indexedDocumentCount = computed(
  () => documents.value.filter((document) => document.status === "indexed").length
);

const failedDocumentCount = computed(
  () => documents.value.filter((document) => document.status === "failed").length
);

const filteredDocuments = computed(() => {
  const query = knowledgeSearch.value.trim().toLowerCase();
  return documents.value.filter((document) => {
    const matchesQuery =
      !query ||
      document.name.toLowerCase().includes(query) ||
      document.type.toLowerCase().includes(query) ||
      collectionName(document.collection_id).toLowerCase().includes(query);
    const matchesStatus = knowledgeFilter.value === "all" || document.status === knowledgeFilter.value;
    return matchesQuery && matchesStatus;
  });
});

const selectedPetOption = computed(
  () => petOptions.find((pet) => pet.id === selectedPet.value) ?? petOptions[0]
);

const petMotionClass = computed(() => ({
  listening: isPetMode ? petVoiceState.value === "listening" : isListening.value,
  notifying: Boolean(petBubble.value) && (isPetMode ? petVoiceState.value === "idle" : !isListening.value),
  thinking: isPetMode
    ? petVoiceState.value === "transcribing" || petVoiceState.value === "thinking"
    : runInProgress.value && !isListening.value
}));

const petPanelVisible = computed(() => petVoiceState.value !== "idle");
const petStatusLabel = computed(() => {
  switch (petVoiceState.value) {
    case "listening":
      return "正在聆听，单击结束";
    case "transcribing":
      return "正在识别语音";
    case "thinking":
      return "正在处理";
    case "approval":
      return "需要你的确认";
    case "speaking":
      return "DeskBits";
    case "error":
      return "这次没有完成";
    default:
      return "单击桌宠说话";
  }
});

const mainPetDockStyle = computed(() => {
  if (!mainPetPosition.value) {
    return {};
  }
  return {
    left: `${mainPetPosition.value.left}px`,
    top: `${mainPetPosition.value.top}px`,
    right: "auto",
    bottom: "auto"
  };
});

const runStatus = computed<RunStatus>(() => {
  if (runInProgress.value || activeRunId.value || runtimeRunStatus.value !== "idle") {
    return runtimeRunStatus.value;
  }
  return currentConversation.value?.status ?? "idle";
});

const userInitials = computed(() => {
  const name = profile.value.display_name.trim();
  if (!name) {
    return "LU";
  }
  if (!/\s/.test(name)) {
    return name.slice(0, 2).toUpperCase();
  }
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
});

const enabledSkillCount = computed(() => skills.value.filter((skill) => skill.enabled).length);
const builtinSkills = computed(() => skills.value.filter((skill) => skill.builtin));
const importedSkills = computed(() => skills.value.filter((skill) => !skill.builtin));
const pendingDocumentUploading = computed(() => pendingAttachments.value.some((item) => item.status === "uploading"));

const activeReminderCount = computed(
  () => reminders.value.filter((reminder) => reminder.status === "active").length
);

const todaysReminders = computed(() =>
  reminders.value
    .filter((reminder) => reminder.status !== "deleted" && isReminderDueToday(reminder))
    .sort((a, b) => reminderSortTime(a) - reminderSortTime(b))
);

const recentConversations = computed(() => conversations.value.slice(0, 5));

const sortedFileEntries = computed(() =>
  [...fileEntries.value].sort((a, b) => {
    if (a.type !== b.type) {
      return a.type === "dir" ? -1 : 1;
    }
    return a.name.localeCompare(b.name, "zh-CN");
  })
);

const fileBreadcrumbs = computed(() => {
  const normalized = filePath.value || "/";
  const segments = normalized.split("/").filter(Boolean);
  const crumbs: Array<{ label: string; path: string }> = [{ label: "根目录", path: "/" }];
  let path = "";
  for (const segment of segments) {
    path += `/${segment}`;
    crumbs.push({ label: segment, path });
  }
  return crumbs;
});

const pendingTasks = computed(() => tasks.value.filter((task) => task.status !== "done"));

const runtimeFeatureList = computed(() =>
  Object.entries(runtimeInfo.value?.features ?? {})
    .filter(([, enabled]) => enabled)
    .map(([name]) => name.replace(/_/g, " "))
);

const runtimeKeySource = computed(() =>
  runtimeInfo.value?.model?.api_key_source
    ? `${runtimeInfo.value.model.api_key_source} key`
    : hasBackendToken.value
      ? ".env / local service process"
      : "Local service not connected"
);

const runtimeModelLabel = computed(() => {
  const runtimeModel = runtimeInfo.value?.model;
  if (runtimeModel) {
    return `${runtimeModel.provider} / ${runtimeModel.name}`;
  }
  return `${models.value.provider} / ${models.value.model}`;
});

const composerModelOptions = computed(() => configuredModels.value);

function configuredModelKey(item: Pick<api.ConfiguredModel, "provider" | "model">): string {
  return `${item.provider}::${item.model}`;
}

const speechStatusLabel = computed(() => {
  if (!speechStatus.value && !runtimeInfo.value?.speech) {
    return "Waiting for speech status API";
  }
  const status = speechStatus.value ?? runtimeInfo.value?.speech;
  if (!status?.enabled) {
    return "Disabled";
  }
  return status.model_cached ? `${status.model} ready` : `${status.model} needs preload`;
});

const currentSpeechStatus = computed(() => speechStatus.value ?? runtimeInfo.value?.speech ?? null);

const isSpeechPreloadActive = computed(
  () =>
    speechPreloadStatus.value === "queued" ||
    speechPreloadStatus.value === "downloading" ||
    speechPreloadStatus.value === "loading"
);

const speechPreloadHint = computed(() => {
  const status = currentSpeechStatus.value;
  if (!hasBackendToken.value && !status) {
    return null;
  }
  if (status && !status.enabled) {
    return { kind: "disabled", text: "语音识别组件不可用" };
  }
  if (speechPreloadStatus.value === "failed") {
    return {
      kind: "failed",
      text: speechPreloadError.value
        ? `语音模型下载失败：${speechPreloadError.value}`
        : "语音模型下载失败"
    };
  }
  if (speechPreloadStatus.value === "queued" || speechPreloadStatus.value === "downloading") {
    return { kind: "loading", text: "下载中..." };
  }
  if (speechPreloadStatus.value === "loading") {
    return { kind: "loading", text: "加载中..." };
  }
  if (!status || !status.model_cached) {
    return { kind: "missing", text: "语音模型未下载（首次使用需下载，约466MB）" };
  }
  return null;
});

const storageTotalBytes = computed(() => {
  if (!storageUsage.value) {
    return 0;
  }
  return (
    storageUsage.value.database_bytes +
    storageUsage.value.uploads_bytes +
    storageUsage.value.knowledge_bytes +
    storageUsage.value.traces_bytes
  );
});

const composerPlaceholder = computed(() => {
  if (isListening.value) {
    return "Listening...";
  }
  return "输入消息，或上传文档与 Agent 对话";
});

function continuePlan(): void {
  if (!activePlan.value) return;
  rightSidebarOpen.value = true;
  composerText.value = `请继续完善「${activePlan.value.title}」这个计划，补充风险、依赖和验收标准。`;
}

function executePlan(): void {
  if (!activePlan.value) return;
  if (activeConversationId.value) {
    const nextSteps: PlanStep[] = activePlan.value.steps.map((step, index) => ({
      ...step,
      status: index === 0 ? "in_progress" : step.status === "done" ? "done" : "pending"
    }));
    plansByConversation.value = {
      ...plansByConversation.value,
      [activeConversationId.value]: {
        ...activePlan.value,
        status: "executing",
        completedSteps: activePlan.value.completedSteps > 0 ? activePlan.value.completedSteps : 0,
        steps: nextSteps
      }
    };
  }
  composerText.value = `请按这个计划开始执行：${activePlan.value.title}\n目标：${activePlan.value.objective}\n步骤：${activePlan.value.steps.map((step, index) => `${index + 1}. ${step.title}`).join("；")}`;
}

onMounted(async () => {
  document.documentElement.classList.toggle("pet-window-mode", isPetMode);
  document.body.classList.toggle("pet-window-mode", isPetMode);
  window.addEventListener("storage", handlePetStorageChange);
  window.addEventListener("resize", clampMainPetDockToViewport);
  loadSelectedPet();
  await nextTick();
  loadMainPetPosition();
  await loadPetFloatEnabled();

  if (isPetMode) {
    await loadPetRuntimeState();
    startRuntimeSocket();
    scheduleIdlePetBubble();
    return;
  }

  await loadBackendState();
  startRuntimeSocket();
  scheduleIdlePetBubble();
});

onUnmounted(() => {
  document.documentElement.classList.remove("pet-window-mode");
  document.body.classList.remove("pet-window-mode");
  window.removeEventListener("storage", handlePetStorageChange);
  window.removeEventListener("resize", clampMainPetDockToViewport);
  stopRuntimeSocket();
  clearSpeechPreloadPoll();
  clearPetTimers();
  clearPetVoiceTimers();
  resetMainPetDragState(false);
  if (attachmentToastTimer) window.clearTimeout(attachmentToastTimer);
  if (skillImportProgressTimer) window.clearInterval(skillImportProgressTimer);
  petSocket.value?.close();
  void window.desktopMate?.stopPetSpeech?.();
});

function loadSelectedPet() {
  const storedPet = window.localStorage.getItem(selectedPetStorageKey);
  if (isPetVariant(storedPet)) {
    selectedPet.value = storedPet;
  }
}

function handlePetStorageChange(event: StorageEvent) {
  if (event.key === selectedPetStorageKey && isPetVariant(event.newValue)) {
    selectedPet.value = event.newValue;
  }
}

async function loadPetFloatEnabled() {
  if (!window.desktopMate?.getPetFloatEnabled) {
    petFloatEnabled.value = true;
    return;
  }
  try {
    petFloatEnabled.value = await window.desktopMate.getPetFloatEnabled();
    petFloatSettingError.value = "";
  } catch (error) {
    petFloatEnabled.value = true;
    petFloatSettingError.value = error instanceof Error ? error.message : String(error);
  }
}

async function savePetFloatEnabled() {
  if (!window.desktopMate?.setPetFloatEnabled) {
    return;
  }
  try {
    petFloatEnabled.value = await window.desktopMate.setPetFloatEnabled(petFloatEnabled.value);
    petFloatSettingError.value = "";
  } catch (error) {
    petFloatSettingError.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadPetRuntimeState() {
  try {
    const config = await api.resolveAgentConfig();
    hasBackendToken.value = Boolean(config.token);
    if (!config.token) {
      connectionStatus.value = "local";
      return;
    }
    connectionStatus.value = "connected";
    const [runtime, status] = await Promise.all([api.fetchRuntime(), api.fetchRuntimeStatus()]);
    runtimeInfo.value = runtime;
    applyRuntimeStatus(status);
  } catch (error) {
    connectionStatus.value = "error";
    runtimeLastMessage.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadBackendState() {
  try {
    const config = await api.resolveAgentConfig();
    hasBackendToken.value = Boolean(config.token);
    if (!config.token) {
      backendError.value = "";
      connectionStatus.value = "local";
    }
    healthInfo.value = await api.fetchHealth();
    runtimeInfo.value = await api.fetchRuntime();
    if (!config.token) {
      return;
    }
    connectionStatus.value = "connected";
    await Promise.all([
      loadSessions(),
      loadProjects(),
      loadDocuments(),
      loadCollections(),
      loadSkills(),
      loadProfile(),
      loadModels(),
      loadConfiguredModels(),
      loadApiKeys(),
      loadRuntimePanel(),
      loadReminders(),
      loadTasks(),
      loadArtifacts()
    ]);
    syncSelectedModel();
    startRuntimeSocket();
  } catch (error) {
    if (!hasBackendToken.value) {
      connectionStatus.value = "local";
      backendError.value = "";
      return;
    }
    connectionStatus.value = "error";
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

function runtimeStatusToRunStatus(status: api.RuntimeStatus): RunStatus {
  if (status.pending_permissions > 0) {
    return "waiting_approval";
  }
  if (status.active_runs > 0 || status.last_run_status === "running") {
    return "thinking";
  }
  if (status.last_run_status === "completed") {
    return "done";
  }
  if (status.last_run_status === "failed") {
    return "failed";
  }
  return "idle";
}

function applyRuntimeStatus(status: api.RuntimeStatus) {
  runtimeStatus.value = status;
  runtimeLastMessage.value = status.last_message ?? "";
  applyRuntimeRunStatus(runtimeStatusToRunStatus(status));
}

function applySpeechStatus(status: api.SpeechStatus) {
  speechStatus.value = status;
  if (status.model_cached) {
    clearSpeechPreloadPoll();
    speechPreloadJobId.value = "";
    speechPreloadStatus.value = null;
    speechPreloadError.value = "";
  }
}

async function refreshSpeechStatus() {
  if (!hasBackendToken.value) {
    return;
  }
  try {
    applySpeechStatus(await api.fetchSpeechStatus());
  } catch {
    // The composer hint remains actionable when the status endpoint is temporarily unavailable.
  }
}

function clearSpeechPreloadPoll() {
  if (speechPreloadPollTimer !== null) {
    window.clearTimeout(speechPreloadPollTimer);
    speechPreloadPollTimer = null;
  }
}

function scheduleSpeechPreloadPoll() {
  clearSpeechPreloadPoll();
  speechPreloadPollTimer = window.setTimeout(() => {
    void pollSpeechPreloadJob();
  }, 1500);
}

async function pollSpeechPreloadJob() {
  if (!speechPreloadJobId.value) {
    return;
  }
  speechPreloadPolling.value = true;
  try {
    const job = await api.getSpeechPreloadJob(speechPreloadJobId.value);
    speechPreloadStatus.value = job.status;
    speechPreloadError.value = job.error ?? "";
    if (job.status === "ready") {
      clearSpeechPreloadPoll();
      await refreshSpeechStatus();
      return;
    }
    if (job.status === "failed") {
      clearSpeechPreloadPoll();
      return;
    }
    scheduleSpeechPreloadPoll();
  } catch (error) {
    clearSpeechPreloadPoll();
    speechPreloadStatus.value = "failed";
    speechPreloadError.value = error instanceof Error ? error.message : String(error);
  } finally {
    speechPreloadPolling.value = false;
  }
}

async function startSpeechPreload() {
  if (isSpeechPreloadActive.value || speechPreloadPolling.value) {
    return;
  }
  speechPreloadError.value = "";
  speechPreloadStatus.value = "queued";
  clearSpeechPreloadPoll();
  try {
    const job = await api.preloadSpeechModel();
    speechPreloadJobId.value = job.job_id;
    await pollSpeechPreloadJob();
  } catch (error) {
    speechPreloadStatus.value = "failed";
    speechPreloadError.value = error instanceof Error ? error.message : String(error);
  }
}

function applyRuntimeRunStatus(status: RunStatus, sessionId?: string) {
  runtimeRunStatus.value = status;
  runInProgress.value =
    status === "thinking" || status === "using_tool" || status === "waiting_approval";
  if (sessionId) {
    updateConversationStatus(sessionId, status);
  } else if (activeConversationId.value) {
    updateConversationStatus(activeConversationId.value, status);
  }
  if (runInProgress.value && idlePetBubbleTimer) {
    window.clearTimeout(idlePetBubbleTimer);
    idlePetBubbleTimer = null;
  }
  if (!runInProgress.value) {
    scheduleIdlePetBubble();
  }
}

function updateRuntimeStatusPatch(patch: Partial<api.RuntimeStatus>) {
  const previous = runtimeStatus.value;
  applyRuntimeStatus({
    active_runs: patch.active_runs ?? previous?.active_runs ?? 0,
    last_run_status: patch.last_run_status ?? previous?.last_run_status ?? null,
    last_message: patch.last_message ?? previous?.last_message ?? null,
    pending_permissions: patch.pending_permissions ?? previous?.pending_permissions ?? 0,
    due_reminders: patch.due_reminders ?? previous?.due_reminders ?? 0
  });
}

function appendRuntimeEvent(event: api.RuntimeEvent) {
  runtimeEvents.value = [
    event,
    ...runtimeEvents.value.filter((item) => item.id !== event.id)
  ].slice(0, 50);
}

function runtimeEventFromSocket(message: RuntimeSocketMessage): api.RuntimeEvent | null {
  if (message.type === "runtime_status") {
    return null;
  }
  return {
    id: message.id ?? `ws-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type: message.type,
    created_at: message.created_at ?? new Date().toISOString(),
    payload: message.payload ?? {}
  };
}

function handleRuntimeSocketMessage(message: RuntimeSocketMessage) {
  const payload = message.payload ?? {};
  if (message.type === "runtime_status") {
    const snapshot = payload.snapshot;
    applyRuntimeStatus(
      (typeof snapshot === "object" && snapshot !== null ? snapshot : payload) as api.RuntimeStatus
    );
    return;
  }

  const event = runtimeEventFromSocket(message);
  if (event) {
    appendRuntimeEvent(event);
  }

  if (message.type === "run_started") {
    const runId = String(payload.run_id ?? "");
    const sessionId = String(payload.session_id ?? "");
    activeRunId.value = runId || activeRunId.value;
    updateRuntimeStatusPatch({
      active_runs: Math.max(runtimeStatus.value?.active_runs ?? 0, 1),
      last_run_status: "running",
      last_message: "DeskBits started working."
    });
    applyRuntimeRunStatus("thinking", sessionId || undefined);
    return;
  }

  if (message.type === "run_progress") {
    const runId = String(payload.run_id ?? "");
    if (runId) {
      activeRunId.value = runId;
    }
    if (payload.stage === "tool") {
      const toolName = String(payload.tool ?? "tool");
      runtimeLastMessage.value = `Using ${toolName}`;
      updateRuntimeStatusPatch({
        active_runs: Math.max(runtimeStatus.value?.active_runs ?? 0, 1),
        last_run_status: "running",
        last_message: runtimeLastMessage.value
      });
      applyRuntimeRunStatus("using_tool");
      showPetBubble("idea", 2600);
      return;
    }
    if (payload.stage === "answer") {
      runtimeLastMessage.value = String(payload.text ?? runtimeLastMessage.value);
      updateRuntimeStatusPatch({
        active_runs: Math.max(runtimeStatus.value?.active_runs ?? 0, 1),
        last_run_status: "running",
        last_message: runtimeLastMessage.value
      });
      applyRuntimeRunStatus("thinking");
    }
    return;
  }

  if (message.type === "run_completed") {
    const sessionId = String(payload.session_id ?? "");
    activeRunId.value = null;
    updateRuntimeStatusPatch({
      active_runs: 0,
      last_run_status: "completed",
      last_message: "Run completed."
    });
    applyRuntimeRunStatus("done", sessionId || undefined);
    showPetBubble("like", 2600);
    void refreshSessionsAfterRun();
    return;
  }

  if (message.type === "run_failed") {
    const reason = String(payload.reason ?? "Run failed.");
    activeRunId.value = null;
    updateRuntimeStatusPatch({
      active_runs: 0,
      last_run_status: "failed",
      last_message: reason.slice(0, 160)
    });
    applyRuntimeRunStatus("failed");
    showPetBubble("heart", 3200);
    return;
  }

  if (message.type === "error") {
    const code = String(payload.code ?? "");
    const reason = String(payload.message ?? "Runtime WebSocket error.");
    runtimeLastMessage.value = reason.slice(0, 160);
    if (code.toLowerCase().includes("auth") || code === "401" || code === "403") {
      runtimeSocketStopped = true;
      connectionStatus.value = "error";
      backendError.value = reason;
      runtimeSocket.value?.close();
    }
  }
}

function startRuntimeSocket() {
  if (!hasBackendToken.value || runtimeSocketStopped || runtimeSocketConnecting || runtimeSocket.value) {
    return;
  }
  runtimeSocketConnecting = true;
  void api
    .openRuntimeSocket()
    .then((socket) => {
      runtimeSocketConnecting = false;
      runtimeSocket.value = socket;
      runtimeReconnectAttempt = 0;
      connectionStatus.value = "connected";
      socket.addEventListener("message", (event) => {
        try {
          handleRuntimeSocketMessage(JSON.parse(event.data) as RuntimeSocketMessage);
        } catch (error) {
          runtimeLastMessage.value = error instanceof Error ? error.message : String(error);
        }
      });
      socket.addEventListener("close", (event) => {
        runtimeSocket.value = null;
        if (event.code === 1008 || event.code === 4401 || event.code === 4403) {
          runtimeSocketStopped = true;
          connectionStatus.value = "error";
          backendError.value = "Runtime WebSocket authentication failed.";
          return;
        }
        scheduleRuntimeReconnect();
      });
      socket.addEventListener("error", () => {
        runtimeLastMessage.value = "Runtime WebSocket connection failed.";
      });
    })
    .catch((error) => {
      runtimeSocketConnecting = false;
      runtimeLastMessage.value = error instanceof Error ? error.message : String(error);
      scheduleRuntimeReconnect();
    });
}

function scheduleRuntimeReconnect() {
  if (runtimeSocketStopped || !hasBackendToken.value || runtimeReconnectTimer) {
    return;
  }
  const delays = [1000, 3000, 10000];
  const delay = delays[Math.min(runtimeReconnectAttempt, delays.length - 1)];
  runtimeReconnectAttempt += 1;
  runtimeReconnectTimer = window.setTimeout(() => {
    runtimeReconnectTimer = null;
    startRuntimeSocket();
  }, delay);
}

function stopRuntimeSocket() {
  runtimeSocketStopped = true;
  if (runtimeReconnectTimer) {
    window.clearTimeout(runtimeReconnectTimer);
    runtimeReconnectTimer = null;
  }
  runtimeSocket.value?.close();
  runtimeSocket.value = null;
  runtimeSocketConnecting = false;
}

// ---------- Sessions ----------

async function loadSessions() {
  const { sessions } = await api.fetchSessions("recent");
  const selectedSessionId = activeConversationId.value ?? sessions[0]?.id ?? null;
  conversations.value = sessions.map((session) => conversationFromSession(session, selectedSessionId));
  if (selectedSessionId && selectedSessionId !== activeConversationId.value) {
    await selectConversation(selectedSessionId);
  } else if (selectedSessionId) {
    scrollMessagesToBottom(true);
  }
}

async function selectConversation(conversationId: string) {
  const nextConversation = conversations.value.find((conversation) => conversation.id === conversationId);
  conversations.value = conversations.value.map((conversation) => ({
    ...conversation,
    active: conversation.id === conversationId
  }));
  activeConversationId.value = conversationId;
  activeProjectId.value = nextConversation?.projectId ?? null;
  activeArtifact.value = null;
  activeWorkspaceItem.value = null;
  activeView.value = nextConversation?.projectId ? "project" : "chat";
  if (nextConversation?.projectId) {
    expandedProjectIds.value = { ...expandedProjectIds.value, [nextConversation.projectId]: true };
    void loadProjectSessions(nextConversation.projectId);
    const project = projects.value.find((item) => item.id === nextConversation.projectId);
    if (project) void loadProjectRoot(project);
  }
  if (!messagesByConversation.value[conversationId]) {
    try {
      const { messages } = await api.fetchSessionMessages(conversationId);
      messagesByConversation.value = {
        ...messagesByConversation.value,
        [conversationId]: messages.map((message) => ({
          id: message.id,
          role: message.role,
          text: message.text,
          runId: messageRunId(message.metadata),
          meta: formatMeta(message.metadata),
          attachments: formatAttachments(message.metadata)
        }))
      };
      restoreConversationOutputs(conversationId, messages);
      scrollMessagesToBottom(true);
    } catch {
      messagesByConversation.value = { ...messagesByConversation.value, [conversationId]: [] };
    }
  } else {
    scrollMessagesToBottom(true);
  }
}

async function startNewChat() {
  activeArtifact.value = null;
  activeWorkspaceItem.value = null;
  activeConversationId.value = null;
  activeProjectId.value = null;
  conversations.value = conversations.value.map((conversation) => ({ ...conversation, active: false }));
  composerText.value = "";
  pendingImages.value = [];
  imageUploadError.value = "";
  pendingPermission.value = null;
  activeView.value = "chat";
}

async function deleteConversation(conversationId: string) {
  if (!hasBackendToken.value) {
    conversations.value = conversations.value.filter((conversation) => conversation.id !== conversationId);
    projectSessionsByProject.value = Object.fromEntries(
      Object.entries(projectSessionsByProject.value).map(([projectId, sessions]) => [
        projectId,
        sessions.filter((session) => session.id !== conversationId)
      ])
    );
    delete messagesByConversation.value[conversationId];
    delete timelineByConversation.value[conversationId];
    delete conversationOutputsByConversation.value[conversationId];
    delete toolCardsByConversation.value[conversationId];
    if (activeConversationId.value === conversationId) {
      activeConversationId.value = null;
      composerText.value = "";
    }
    return;
  }
  try {
    await api.deleteSession(conversationId);
    conversations.value = conversations.value.filter((conversation) => conversation.id !== conversationId);
    projectSessionsByProject.value = Object.fromEntries(
      Object.entries(projectSessionsByProject.value).map(([projectId, sessions]) => [
        projectId,
        sessions.filter((session) => session.id !== conversationId)
      ])
    );
    delete conversationOutputsByConversation.value[conversationId];
    if (activeConversationId.value === conversationId) {
      activeConversationId.value = null;
      composerText.value = "";
    }
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function ensureSession(): Promise<string> {
  if (activeConversationId.value) {
    return activeConversationId.value;
  }
  if (activeProject.value) {
    const { session } = await api.createProjectSession(activeProject.value.id);
    projectSessionsByProject.value = {
      ...projectSessionsByProject.value,
      [activeProject.value.id]: [session, ...projectSessions(activeProject.value.id).filter((item) => item.id !== session.id)]
    };
    upsertConversationFromSession(session);
    activeConversationId.value = session.id;
    return session.id;
  }
  // 使用客户端临时 id；服务端仅在首条消息实际到达时才创建持久会话。
  return createLocalConversation("New Conversation");
}

function openSettings(panel: SettingsPanel) {
  activeSettingsPanel.value = panel;
  activeView.value = "settings";
}

function selectPet(pet: PetVariant) {
  selectedPet.value = pet;
  window.localStorage.setItem(selectedPetStorageKey, pet);
}

function interactWithPet() {
  showPetBubble(Math.random() > 0.5 ? "heart" : "smile", 2600);
}

function mainPetDockSize() {
  const rect = mainPetDock.value?.getBoundingClientRect();
  return {
    width: rect?.width || 132,
    height: rect?.height || 108
  };
}

function clampMainPetPosition(left: number, top: number) {
  const margin = 8;
  const { width, height } = mainPetDockSize();
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const maxTop = Math.max(margin, window.innerHeight - height - margin);
  return {
    left: Math.min(Math.max(left, margin), maxLeft),
    top: Math.min(Math.max(top, margin), maxTop)
  };
}

function currentMainPetPosition() {
  const rect = mainPetDock.value?.getBoundingClientRect();
  if (rect) {
    return clampMainPetPosition(rect.left, rect.top);
  }
  const { width, height } = mainPetDockSize();
  return clampMainPetPosition(window.innerWidth - width - 24, window.innerHeight - height - 18);
}

function saveMainPetPosition() {
  if (!mainPetPosition.value) return;
  window.localStorage.setItem(mainPetPositionStorageKey, JSON.stringify(mainPetPosition.value));
}

function loadMainPetPosition() {
  const raw = window.localStorage.getItem(mainPetPositionStorageKey);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as { left?: unknown; top?: unknown };
    if (typeof parsed.left !== "number" || typeof parsed.top !== "number") {
      return;
    }
    mainPetPosition.value = clampMainPetPosition(parsed.left, parsed.top);
  } catch {
    window.localStorage.removeItem(mainPetPositionStorageKey);
  }
}

function clampMainPetDockToViewport() {
  if (!mainPetPosition.value) return;
  mainPetPosition.value = clampMainPetPosition(mainPetPosition.value.left, mainPetPosition.value.top);
  saveMainPetPosition();
}

function clearMainPetDragTimer() {
  if (mainPetDragTimer !== null) {
    window.clearTimeout(mainPetDragTimer);
    mainPetDragTimer = null;
  }
}

function resetMainPetDragState(suppressClick = mainPetDragging.value || mainPetDragReady) {
  clearMainPetDragTimer();
  mainPetPointerDown = false;
  mainPetDragReady = false;
  mainPetDragging.value = false;
  mainPetPointerId = null;
  mainPetDragStartX = 0;
  mainPetDragStartY = 0;
  mainPetDragLastX = 0;
  mainPetDragLastY = 0;
  if (suppressClick) {
    mainPetSuppressNextClick = true;
  }
}

function handleMainPetPointerDown(event: PointerEvent) {
  if (event.pointerType === "mouse" && event.button !== 0) {
    return;
  }
  mainPetPointerDown = true;
  mainPetDragReady = false;
  mainPetDragging.value = false;
  mainPetPointerId = event.pointerId;
  mainPetDragStartX = event.clientX;
  mainPetDragStartY = event.clientY;
  mainPetDragLastX = event.clientX;
  mainPetDragLastY = event.clientY;
  clearMainPetDragTimer();
  try {
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  } catch {
    // Pointer capture is best effort; moving still works while the pointer remains over the pet.
  }
  mainPetDragTimer = window.setTimeout(() => {
    if (mainPetPointerDown && mainPetPointerId === event.pointerId) {
      mainPetDragReady = true;
    }
  }, petDragHoldDelay);
}

function handleMainPetPointerMove(event: PointerEvent) {
  if (!mainPetPointerDown || mainPetPointerId !== event.pointerId) {
    return;
  }
  const moved =
    Math.abs(event.clientX - mainPetDragStartX) >= petDragThreshold ||
    Math.abs(event.clientY - mainPetDragStartY) >= petDragThreshold;
  if (!mainPetDragging.value && mainPetDragReady && moved) {
    mainPetDragging.value = true;
    mainPetSuppressNextClick = true;
    mainPetPosition.value = currentMainPetPosition();
  }
  if (!mainPetDragging.value || !mainPetPosition.value) {
    return;
  }
  event.preventDefault();
  const deltaX = event.clientX - mainPetDragLastX;
  const deltaY = event.clientY - mainPetDragLastY;
  mainPetDragLastX = event.clientX;
  mainPetDragLastY = event.clientY;
  mainPetPosition.value = clampMainPetPosition(
    mainPetPosition.value.left + deltaX,
    mainPetPosition.value.top + deltaY
  );
}

function handleMainPetPointerUp(event: PointerEvent) {
  if (!mainPetPointerDown || mainPetPointerId !== event.pointerId) {
    return;
  }
  try {
    (event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId);
  } catch {
    // Matching release for best-effort capture.
  }
  const shouldSave = mainPetDragging.value && mainPetPosition.value;
  const shouldSuppressClick = mainPetDragging.value || mainPetDragReady;
  resetMainPetDragState(shouldSuppressClick);
  if (shouldSave) {
    saveMainPetPosition();
  }
}

function handleMainPetPointerCancel() {
  resetMainPetDragState(true);
}

function handleMainPetClick() {
  if (mainPetSuppressNextClick) {
    mainPetSuppressNextClick = false;
    return;
  }
  interactWithPet();
}

function handleMainPetBubbleClick() {
  if (mainPetSuppressNextClick) {
    mainPetSuppressNextClick = false;
    return;
  }
  activeView.value = "chat";
}

async function restoreFromPetWindow() {
  clearPetVoiceTimers();
  if (isListening.value) {
    discardRecordedVoice = true;
    await stopVoiceInput();
  }
  interactWithPet();
  resetPetVoiceInteraction();
  void window.desktopMate?.restoreMainWindow?.();
}

function clearPetVoiceTimers() {
  if (petClickTimer !== null) {
    window.clearTimeout(petClickTimer);
    petClickTimer = null;
  }
  if (petDragTimer !== null) {
    window.clearTimeout(petDragTimer);
    petDragTimer = null;
  }
  if (petCollapseTimer !== null) {
    window.clearTimeout(petCollapseTimer);
    petCollapseTimer = null;
  }
}

function setPetWindowMode(mode: "compact" | "conversation") {
  if (isPetMode) {
    void window.desktopMate?.setPetWindowMode?.(mode);
  }
}

function movePetWindow(deltaX: number, deltaY: number) {
  if (isPetMode && (deltaX !== 0 || deltaY !== 0)) {
    void window.desktopMate?.movePetWindow?.(deltaX, deltaY);
  }
}

function resetPetVoiceInteraction() {
  clearPetVoiceTimers();
  resetPetDragState(false);
  petVoiceState.value = "idle";
  petTranscript.value = "";
  petReply.value = "";
  petError.value = "";
  petPermission.value = null;
  petReplyStreaming = false;
  discardRecordedVoice = false;
  void window.desktopMate?.stopPetSpeech?.();
  setPetWindowMode("compact");
}

function resetPetDragState(suppressClick = petDragging || petDragReady) {
  if (petDragTimer !== null) {
    window.clearTimeout(petDragTimer);
    petDragTimer = null;
  }
  petPointerDown = false;
  petDragReady = false;
  petDragging = false;
  petDragPointerId = null;
  petDragStartX = 0;
  petDragStartY = 0;
  petDragLastX = 0;
  petDragLastY = 0;
  if (suppressClick) {
    petSuppressNextClick = true;
  }
}

function schedulePetCollapse() {
  if (petPermission.value || petVoiceState.value === "listening") {
    return;
  }
  if (petCollapseTimer !== null) {
    window.clearTimeout(petCollapseTimer);
  }
  petCollapseTimer = window.setTimeout(() => {
    resetPetVoiceInteraction();
  }, 8000);
}

function handlePetPointerDown(event: PointerEvent) {
  if (!isPetMode || event.button !== 0 || petVoiceState.value === "approval") {
    return;
  }
  petPointerDown = true;
  petDragReady = false;
  petDragging = false;
  petDragPointerId = event.pointerId;
  petDragStartX = event.screenX;
  petDragStartY = event.screenY;
  petDragLastX = event.screenX;
  petDragLastY = event.screenY;
  if (petDragTimer !== null) {
    window.clearTimeout(petDragTimer);
  }
  try {
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  } catch {
    // Pointer capture is best effort; drag still works while the pointer remains over the pet.
  }
  petDragTimer = window.setTimeout(() => {
    if (petPointerDown && petDragPointerId === event.pointerId) {
      petDragReady = true;
    }
  }, petDragHoldDelay);
}

function handlePetPointerMove(event: PointerEvent) {
  if (!petPointerDown || petDragPointerId !== event.pointerId) {
    return;
  }
  const moved =
    Math.abs(event.screenX - petDragStartX) >= petDragThreshold ||
    Math.abs(event.screenY - petDragStartY) >= petDragThreshold;
  if (!petDragging && petDragReady && moved) {
    petDragging = true;
    petSuppressNextClick = true;
    if (petClickTimer !== null) {
      window.clearTimeout(petClickTimer);
      petClickTimer = null;
    }
  }
  if (!petDragging) {
    return;
  }
  event.preventDefault();
  const deltaX = event.screenX - petDragLastX;
  const deltaY = event.screenY - petDragLastY;
  petDragLastX = event.screenX;
  petDragLastY = event.screenY;
  movePetWindow(deltaX, deltaY);
}

function handlePetPointerUp(event: PointerEvent) {
  if (!petPointerDown || petDragPointerId !== event.pointerId) {
    return;
  }
  try {
    (event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId);
  } catch {
    // Matching release for best-effort capture.
  }
  const shouldSuppressClick = petDragging || petDragReady;
  resetPetDragState(shouldSuppressClick);
}

function handlePetPointerCancel() {
  resetPetDragState(true);
}

function togglePetVoiceInteraction() {
  if (!isPetMode || petVoiceState.value === "approval") {
    return;
  }

  if (isListening.value) {
    void stopVoiceInput();
    return;
  }

  if (petVoiceState.value === "transcribing" || petVoiceState.value === "thinking") {
    return;
  }

  if (petVoiceState.value === "speaking") {
    stopPetSpeech();
    petVoiceState.value = "idle";
    schedulePetCollapse();
    return;
  }

  stopPetSpeech();
  petReply.value = "";
  petError.value = "";
  petTranscript.value = "";
  petVoiceState.value = "listening";
  setPetWindowMode("conversation");
  void startVoiceInput("pet");
}

function handlePetClick(event: MouseEvent) {
  if (!isPetMode) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (petSuppressNextClick) {
    petSuppressNextClick = false;
    return;
  }
  if (petClickTimer !== null) {
    window.clearTimeout(petClickTimer);
    petClickTimer = null;
    return;
  }
  petClickTimer = window.setTimeout(() => {
    petClickTimer = null;
    togglePetVoiceInteraction();
  }, petClickDelay);
}

function handlePetDoubleClick(event: MouseEvent) {
  if (!isPetMode) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (petClickTimer !== null) {
    window.clearTimeout(petClickTimer);
    petClickTimer = null;
  }
  void restoreFromPetWindow();
}

function petConversationId() {
  const existing = window.localStorage.getItem(petSessionStorageKey);
  if (existing) {
    return existing;
  }
  const sessionId = `pet-${crypto.randomUUID()}`;
  window.localStorage.setItem(petSessionStorageKey, sessionId);
  return sessionId;
}

function petSpeechText(text: string) {
  return text
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[#*_`>-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 300);
}

function speakPetReply(text: string) {
  const speech = petSpeechText(text);
  if (speech) {
    void window.desktopMate?.speakPetReply?.(speech);
  }
}

function stopPetSpeech() {
  void window.desktopMate?.stopPetSpeech?.();
}

async function sendPetCommand(text: string) {
  if (!hasBackendToken.value || connectionStatus.value === "error") {
    petVoiceState.value = "error";
    petError.value = "无法连接本地 Agent 服务。";
    schedulePetCollapse();
    return;
  }
  petTranscript.value = text;
  petReply.value = "";
  petError.value = "";
  petPermission.value = null;
  petReplyStreaming = false;
  petVoiceState.value = "thinking";
  setPetWindowMode("conversation");

  try {
    const socket = await api.openChatSocket();
    petSocket.value?.close();
    petSocket.value = socket;
    socket.addEventListener("message", (event) => {
      handlePetServerEvent(JSON.parse(event.data) as ServerEvent);
    });
    socket.addEventListener("close", () => {
      if (petSocket.value === socket) {
        petSocket.value = null;
      }
    });
    socket.send(
      JSON.stringify({
        type: "user_message",
        session_id: petConversationId(),
        text,
        mode: agentMode,
        surface: "pet"
      })
    );
  } catch (error) {
    petVoiceState.value = "error";
    petError.value = error instanceof Error ? error.message : "无法连接本地 Agent 服务。";
    schedulePetCollapse();
  }
}

function handlePetServerEvent(event: ServerEvent) {
  const payload = event.payload ?? {};
  if (event.type === "agent_delta") {
    if (!petReplyStreaming) {
      petReply.value = "";
      petReplyStreaming = true;
    }
    petReply.value += String(payload.text ?? "");
    return;
  }
  if (event.type === "tool_call_started") {
    petReplyStreaming = false;
    petVoiceState.value = "thinking";
    petReply.value = `正在使用 ${String(payload.tool_name ?? "工具")}…`;
    return;
  }
  if (event.type === "permission_request") {
    petVoiceState.value = "approval";
    petPermission.value = {
      requestId: String(payload.request_id ?? ""),
      toolName: String(payload.tool_name ?? "操作"),
      summary: String(payload.summary ?? "此操作需要你的确认。"),
      riskLevel: String(payload.risk_level ?? "write")
    };
    return;
  }
  if (event.type === "agent_message") {
    const reply = String(payload.text ?? "").trim();
    petReplyStreaming = false;
    if (reply) {
      petReply.value = reply;
      petVoiceState.value = "speaking";
      speakPetReply(reply);
    }
    return;
  }
  if (event.type === "done") {
    void refreshSessionsAfterRun();
    schedulePetCollapse();
    return;
  }
  if (event.type === "error") {
    petVoiceState.value = "error";
    petError.value = String(payload.message ?? "这次任务没有完成。");
    schedulePetCollapse();
  }
}

function decidePetPermission(decision: "approved" | "denied") {
  const permission = petPermission.value;
  if (!permission || !petSocket.value) {
    return;
  }
  petSocket.value.send(
    JSON.stringify({
      type: "permission_decision",
      request_id: permission.requestId,
      decision
    })
  );
  petPermission.value = null;
  petReplyStreaming = false;
  petVoiceState.value = "thinking";
  petReply.value = decision === "approved" ? "已确认，正在继续。" : "已取消这项操作。";
  if (decision === "denied") {
    schedulePetCollapse();
  }
}

function showPetBubble(kind: PetBubbleKind, durationMs = 2600) {
  if (petBubbleTimer) {
    window.clearTimeout(petBubbleTimer);
  }
  if (idlePetBubbleTimer) {
    window.clearTimeout(idlePetBubbleTimer);
    idlePetBubbleTimer = null;
  }
  petBubble.value = petBubbles[kind];
  petBubbleTimer = window.setTimeout(() => {
    petBubble.value = null;
    petBubbleTimer = null;
    scheduleIdlePetBubble();
  }, durationMs);
}

function scheduleIdlePetBubble() {
  if (idlePetBubbleTimer || runInProgress.value || isListening.value) {
    return;
  }
  idlePetBubbleTimer = window.setTimeout(() => {
    idlePetBubbleTimer = null;
    if (runInProgress.value || isListening.value) {
      scheduleIdlePetBubble();
      return;
    }
    showPetBubble(Math.random() > 0.5 ? "heart" : "smile", 2300);
  }, 9000 + Math.random() * 7000);
}

function clearPetTimers() {
  if (petBubbleTimer) {
    window.clearTimeout(petBubbleTimer);
    petBubbleTimer = null;
  }
  if (idlePetBubbleTimer) {
    window.clearTimeout(idlePetBubbleTimer);
    idlePetBubbleTimer = null;
  }
}

function createLocalConversation(title: string) {
  const conversationId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  conversations.value = [
    {
      id: conversationId,
      title,
      date: displayDate(new Date().toISOString()),
      status: "idle",
      mode: agentMode,
      active: true
    },
    ...conversations.value.map((conversation) => ({ ...conversation, active: false }))
  ];
  messagesByConversation.value = { ...messagesByConversation.value, [conversationId]: [] };
  timelineByConversation.value = { ...timelineByConversation.value, [conversationId]: [] };
  activeConversationId.value = conversationId;
  return conversationId;
}
// ---------- Messages / chat ----------

async function sendMessage() {
  if (pendingDocumentUploading.value) {
    showAttachmentToast("附件仍在上传中，请稍候发送");
    return;
  }
  if (!composerText.value.trim() && pendingImages.value.length === 0 && readyPendingAttachments().length === 0) {
    return;
  }
  const text = composerText.value.trim() || "请分析我附上的文件。";
  const artifactId = pendingArtifactRevisionId.value;
  const manualWorkspaceContext = pendingWorkspaceContext.value;
  const projectForMessage = activeConversationProject.value ?? activeProject.value;
  let attachments: api.AttachmentItem[] = [];
  if (pendingImages.value.length > 0) {
    imageUploadError.value = "";
    try {
      const uploaded = await api.uploadImages(pendingImages.value.map((image) => image.file));
      attachments = [...attachments, ...uploaded.attachments];
    } catch (error) {
      imageUploadError.value = error instanceof Error ? error.message : String(error);
      return;
    }
  }
  attachments = [...attachments, ...readyPendingAttachments()];
  let conversationId: string;
  try {
    // 只有附件上传成功后才创建会话，失败不会留下空白历史。
    conversationId = await ensureSession();
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
    return;
  }
  appendMessage(conversationId, "user", text, undefined, attachments);
  appendTimeline(conversationId, "Queued request", "Agent request", "done");
  composerText.value = "";
  pendingImages.value = [];
  pendingAttachments.value = [];
  pendingArtifactRevisionId.value = null;
  pendingWorkspaceContext.value = null;
  let workspaceContext = manualWorkspaceContext ?? undefined;
  if (projectForMessage) {
    try {
      const projectContext = await projectWorkspaceContextFor(projectForMessage);
      workspaceContext = mergeWorkspaceContexts(projectContext, manualWorkspaceContext);
    } catch (error) {
      appendMessage(
        conversationId,
        "system",
        `项目上下文生成失败：${error instanceof Error ? error.message : "无法读取项目概览"}`
      );
    }
  }

  if (hasBackendToken.value && connectionStatus.value === "connected") {
    void sendToAgent(
      text,
      conversationId,
      attachments,
      artifactId ?? undefined,
      workspaceContext,
      projectForMessage?.id ?? null
    );
  } else {
    updateConversationStatus(conversationId, "done");
    appendLocalAgentNotice(conversationId);
    showPetBubble("like", 2400);
  }
}

function appendMessage(
  conversationId: string,
  role: MessageRole,
  text: string,
  meta?: string,
  attachments?: api.AttachmentItem[],
  runId?: string,
  reasoning?: string
) {
  const now = new Date();
  const message = {
    id: `${now.getTime()}-${Math.random().toString(16).slice(2)}`,
    role,
    text,
    runId,
    meta,
    attachments,
    reasoning
  };
  messagesByConversation.value = {
    ...messagesByConversation.value,
    [conversationId]: [...(messagesByConversation.value[conversationId] ?? []), message]
  };
}

function appendTimeline(
  conversationId: string,
  label: string,
  detail: string,
  status: TimelineItem["status"]
) {
  const item = {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    label,
    detail,
    status
  };
  timelineByConversation.value = {
    ...timelineByConversation.value,
    [conversationId]: [...(timelineByConversation.value[conversationId] ?? []), item]
  };
}

function appendLocalAgentNotice(conversationId: string) {
  const existingNotice = messagesByConversation.value[conversationId]?.some((message) =>
    message.text.includes("Backend is not connected")
  );
  if (!existingNotice) {
    appendMessage(conversationId, "system", "Backend is not connected; message kept locally.", "Local mode");
  }
  appendTimeline(conversationId, "Saved locally", "Backend token is not configured", "done");
}

async function sendToAgent(
  text: string,
  conversationId: string,
  attachments?: api.AttachmentItem[],
  artifactId?: string,
  workspaceContext?: WorkspaceContextPayload,
  projectId?: string | null
) {
  runInProgress.value = true;
  pendingPermission.value = null;
  activeRunId.value = null;
  connectionStatus.value = "connecting";
  updateConversationStatus(conversationId, "thinking");
  appendTimeline(conversationId, "Started agent run", "Connecting to local backend", "active");
  showPetBubble("idea", 2600);

  try {
    const socket = await api.openChatSocket();
    activeSocket.value = socket;
    connectionStatus.value = "connected";
    socket.addEventListener("message", (event) => {
      handleServerEvent(JSON.parse(event.data) as ServerEvent);
    });
    socket.addEventListener("close", () => {
      runInProgress.value = false;
      activeSocket.value = null;
      if (connectionStatus.value === "connecting") {
        connectionStatus.value = "connected";
      }
    });
    socket.send(
      JSON.stringify({
        type: "user_message",
        session_id: conversationId,
        text,
        mode: agentMode,
        attachments: (attachments ?? []).map((attachment) => attachment.id),
        artifact_id: artifactId,
        project_id: projectId ?? null,
        workspace_context: workspaceContext
      })
    );
  } catch {
    runInProgress.value = false;
    activeRunId.value = null;
    connectionStatus.value = "error";
    updateConversationStatus(conversationId, "failed");
    appendTimeline(conversationId, "Agent unavailable", "Could not connect to local backend", "failed");
    appendMessage(conversationId, "system", "Agent service is unavailable. The message is saved locally.");
  }
}

function handleServerEvent(event: ServerEvent) {
  const conversationId = event.session_id ?? activeConversationId.value;
  if (!conversationId) {
    return;
  }
  if (event.run_id) {
    activeRunId.value = event.run_id;
  }
  const payload = event.payload ?? {};

  if (event.type === "agent_thinking") {
    appendThinkingMessage(conversationId, String(payload.text ?? ""), event.run_id);
    return;
  }

  if (event.type === "agent_delta") {
    const delta = String(payload.text ?? "");
    const buffer = (streamingDeltas.value[conversationId] ?? "") + delta;
    streamingDeltas.value = { ...streamingDeltas.value, [conversationId]: buffer };
    setStreamingMessage(conversationId, buffer, event.run_id);
    return;
  }

  if (event.type === "permission_request") {
    updateConversationStatus(conversationId, "waiting_approval");
    const toolName = String(payload.tool_name ?? "tool");
    const requestId = String(payload.request_id ?? "");
    const toolCard = upsertToolCard(conversationId, {
      callId: String(payload.tool_call_id ?? "") || undefined,
      runId: event.run_id,
      requestId,
      toolName,
      riskLevel: String(payload.risk_level ?? defaultToolRisk(toolName)),
      args: asRecord(payload.args) ?? {},
      review: asRecord(payload.review) ?? undefined,
      summary: String(payload.summary ?? "DeskBits 需要你的确认才能继续。"),
      status: "waiting_approval"
    });
    pendingPermission.value = {
      conversationId,
      toolCardId: toolCard.id,
      requestId: String(payload.request_id ?? ""),
      runId: event.run_id,
      toolName,
      riskLevel: toolCard.riskLevel,
      summary: toolCard.summary ?? "DeskBits 需要你的确认才能继续。"
    };
    ensureRunMessageAnchor(conversationId, event.run_id);
    appendTimeline(conversationId, "Waiting for approval", toolName, "waiting");
    return;
  }

  if (event.type === "tool_call_started") {
    const toolName = String(payload.tool_name ?? "tool");
    if (toolName === "plan_draft") {
      // 计划工具开始执行：隐藏右侧栏旧计划，等完成后再显示新计划
      planGeneratingByConversation.value = {
        ...planGeneratingByConversation.value,
        [conversationId]: true
      };
    }
    upsertToolCard(conversationId, {
      callId: String(payload.tool_call_id ?? "") || undefined,
      runId: event.run_id,
      toolName,
      riskLevel: String(payload.risk_level ?? defaultToolRisk(toolName)),
      args: asRecord(payload.args) ?? {},
      status: "running"
    });
    ensureRunMessageAnchor(conversationId, event.run_id);
    updateConversationStatus(conversationId, "using_tool");
    appendTimeline(conversationId, "Using tool", toolName, "active");
    showPetBubble("idea", 2600);
    return;
  }

  if (event.type === "tool_call_finished") {
    const toolName = String(payload.tool_name ?? "");
    const result = asRecord(payload.result) ?? {};
    upsertToolCard(conversationId, {
      callId: String(payload.tool_call_id ?? "") || undefined,
      runId: event.run_id,
      toolName,
      riskLevel: String(payload.risk_level ?? defaultToolRisk(toolName)),
      args: {},
      result,
      summary: String(result.summary ?? "工具已完成"),
      status: "completed"
    });
    if (toolName === "plan_draft") {
      recordConversationPlan(conversationId, payload.result);
      planGeneratingByConversation.value = {
        ...planGeneratingByConversation.value,
        [conversationId]: false
      };
    }
    if (toolName !== "plan_draft") {
      advancePlanProgress(conversationId);
    }
    const output = conversationOutputFromToolResult(
      toolName,
      payload.result,
      String(payload.tool_call_id ?? "")
    );
    if (output) {
      recordConversationOutput(conversationId, output);
      if (!output.taskDraft && activeArtifact.value?.artifactId === output.artifactId) {
        activeArtifact.value = { ...activeArtifact.value, ...output };
      }
      const workspaceItem = activeWorkspaceItem.value;
      if (!output.taskDraft && workspaceItem && workspaceItem.artifactId && output.artifactId && workspaceItem.artifactId === output.artifactId) {
        activeWorkspaceItem.value = {
          ...workspaceItem,
          ...output,
          id: workspaceItem.id,
          savedAt: workspaceItem.savedAt,
          conversationId
        };
      }
      void refreshLiveSourcesAfterTool(toolName);
    }
    appendTimeline(conversationId, "Tool finished", toolName || "tool", "done");
    return;
  }

  if (event.type === "tool_call_failed") {
    const toolName = String(payload.tool_name ?? "tool");
    const result = asRecord(payload.result) ?? {};
    upsertToolCard(conversationId, {
      callId: String(payload.tool_call_id ?? "") || undefined,
      runId: event.run_id,
      toolName,
      riskLevel: String(payload.risk_level ?? defaultToolRisk(toolName)),
      args: {},
      result,
      summary: String(payload.error ?? result.summary ?? "工具执行失败"),
      status: "failed"
    });
    updateConversationStatus(conversationId, "failed");
    appendMessage(conversationId, "system", `Tool failed: ${String(payload.error ?? "unknown error")}`);
    appendTimeline(conversationId, "Tool failed", String(payload.error ?? "unknown error"), "failed");
    if (String(payload.tool_name ?? "") === "plan_draft") {
      // plan_draft 失败：恢复显示旧计划
      planGeneratingByConversation.value = {
        ...planGeneratingByConversation.value,
        [conversationId]: false
      };
    }
    return;
  }

  if (event.type === "agent_message") {
    const reasoning = streamingReasoning(conversationId, event.run_id);
    clearStreamingMessage(conversationId);
    const rawCitations = Array.isArray(payload.citations) ? payload.citations : [];
    const citations = rawCitations
      .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
      .map<Citation>((item) => ({
        title: String(item.title ?? "知识库"),
        source_path: String(item.source_path ?? ""),
        source_type: item.source_type === "web" ? "web" : "knowledge",
        url: String(item.url ?? "") || undefined,
        document_id: String(item.document_id ?? "") || undefined,
        chunk_id: String(item.chunk_id ?? "")
      }));
    citationsByConversation.value = {
      ...citationsByConversation.value,
      [conversationId]: citations
    };
    appendMessage(
      conversationId,
      "assistant",
      String(payload.text ?? ""),
      undefined,
      undefined,
      event.run_id,
      reasoning
    );
    if (activeWorkspaceItem.value?.conversationId === conversationId) {
      const item = activeWorkspaceItem.value;
      workspaceEditMessages.value = {
        ...workspaceEditMessages.value,
        [item.id]: [
          ...(workspaceEditMessages.value[item.id] ?? []),
          {
            id: `workspace-agent-${Date.now()}`,
            role: "assistant",
            text: String(payload.text ?? "")
          }
        ]
      };
    }
    const rawCalls = Array.isArray(payload.tool_calls) ? payload.tool_calls : [];
    const rawResults = Array.isArray(payload.tool_results) ? payload.tool_results : [];
    if (payload.run_status === "permission_denied") {
      rawCalls.forEach((rawCall) => {
        const call = asRecord(rawCall);
        if (!call) return;
        const toolName = String(call.name ?? "tool");
        upsertToolCard(conversationId, {
          callId: String(call.id ?? "") || undefined,
          runId: event.run_id,
          toolName,
          riskLevel: String(call.risk_level ?? defaultToolRisk(toolName)),
          args: asRecord(call.args) ?? {},
          status: "denied"
        });
      });
    }
    rawResults.forEach((rawResult, index) => {
      const call = asRecord(rawCalls[index] ?? rawCalls[0]);
      const output = conversationOutputFromToolResult(
        String(call?.name ?? ""),
        rawResult,
        String(call?.id ?? "")
      );
      if (output) {
        recordConversationOutput(conversationId, output);
      }
    });
    // 普通问答只保留在消息流；右侧栏只显示工具产生的可继续使用结果。
    void refreshSessionsAfterRun();
    return;
  }

  if (event.type === "error") {
    clearStreamingMessage(conversationId);
    updateConversationStatus(conversationId, "failed");
    const code = String(payload.code ?? "");
    const message = String(payload.message ?? "Agent run failed.");
    if (code === "model_unavailable") {
      appendMessage(
        conversationId,
        "system",
        `${message} 已刷新可用模型列表，请在设置中选择一个可用模型后重试。`
      );
      appendTimeline(conversationId, "Model unavailable", String(payload.model ?? models.value.model), "failed");
      void loadAvailableModels(models.value.provider);
    } else {
      appendMessage(conversationId, "system", message);
    }
    runInProgress.value = false;
    activeRunId.value = null;
    // run 失败：恢复显示旧计划，避免右侧栏一直隐藏
    planGeneratingByConversation.value = {
      ...planGeneratingByConversation.value,
      [conversationId]: false
    };
    return;
  }

  if (event.type === "done") {
    updateConversationStatus(conversationId, "done");
    appendTimeline(conversationId, "Run complete", "All events saved to backend trace", "done");
    runInProgress.value = false;
    activeRunId.value = null;
    pendingPermission.value = null;
    showPetBubble("like", 2600);
    // run 结束：若本轮没有 plan_draft 产出，恢复显示旧计划
    planGeneratingByConversation.value = {
      ...planGeneratingByConversation.value,
      [conversationId]: false
    };
    activeSocket.value?.close();
  }
}

function setStreamingMessage(conversationId: string, text: string, runId?: string) {
  const messages = messagesByConversation.value[conversationId] ?? [];
  const streamId = `stream-${runId || conversationId}`;
  const existing = messages.find((message) => message.id === streamId);
  if (existing) {
    messagesByConversation.value = {
      ...messagesByConversation.value,
      [conversationId]: messages.map((message) =>
        message.id === streamId ? { ...message, text, runId: runId ?? message.runId } : message
      )
    };
    return;
  }
  messagesByConversation.value = {
    ...messagesByConversation.value,
    [conversationId]: [
      ...messages,
      {
        id: streamId,
        role: "assistant",
        text,
        runId
      }
    ]
  };
}

function appendThinkingMessage(conversationId: string, text: string, runId?: string) {
  if (!text.trim()) return;
  const messages = messagesByConversation.value[conversationId] ?? [];
  const streamId = `stream-${runId || conversationId}`;
  const existing = messages.find((message) => message.id === streamId);
  if (existing) {
    messagesByConversation.value = {
      ...messagesByConversation.value,
      [conversationId]: messages.map((message) =>
        message.id === streamId
          ? { ...message, runId: runId ?? message.runId, reasoning: `${message.reasoning ?? ""}${text}` }
          : message
      )
    };
  } else {
    messagesByConversation.value = {
      ...messagesByConversation.value,
      [conversationId]: [...messages, { id: streamId, role: "assistant", text: "", runId, reasoning: text }]
    };
  }
  scrollMessagesToBottom();
}

function streamingReasoning(conversationId: string, runId?: string): string | undefined {
  const streamId = `stream-${runId || conversationId}`;
  return messagesByConversation.value[conversationId]?.find((message) => message.id === streamId)?.reasoning;
}

function ensureRunMessageAnchor(conversationId: string, runId?: string) {
  if (!runId) return;
  const messages = messagesByConversation.value[conversationId] ?? [];
  if (messages.some((message) => message.role === "assistant" && message.runId === runId)) return;
  setStreamingMessage(conversationId, streamingDeltas.value[conversationId] ?? "", runId);
}

function clearStreamingMessage(conversationId: string) {
  const messages = messagesByConversation.value[conversationId] ?? [];
  messagesByConversation.value = {
    ...messagesByConversation.value,
    [conversationId]: messages.filter((message) => !message.id.startsWith("stream-"))
  };
  if (streamingDeltas.value[conversationId]) {
    streamingDeltas.value = { ...streamingDeltas.value, [conversationId]: "" };
  }
}

async function refreshSessionsAfterRun() {
  try {
    const { sessions } = await api.fetchSessions("recent");
    conversations.value = sessions.map((session) => {
      const existing = conversations.value.find((item) => item.id === session.id);
      return {
        id: session.id,
        title: session.title,
        date: displayDate(session.updated_at),
        status: existing?.status ?? ("done" as RunStatus),
        mode: session.mode,
        projectId: session.project_id ?? null,
        active: session.id === activeConversationId.value
      };
    });
    if (activeProjectId.value) {
      void loadProjectSessions(activeProjectId.value);
    }
  } catch {
    // 静默失败，下次操作会重试
  }
}

function updateConversationStatus(conversationId: string, status: RunStatus) {
  conversations.value = conversations.value.map((conversation) =>
    conversation.id === conversationId ? { ...conversation, status } : conversation
  );
}

function decidePermission(permission: PermissionRequest, decision: "approved" | "denied") {
  if (!activeSocket.value || permission.conversationId !== activeConversationId.value) {
    return;
  }
  activeSocket.value.send(
    JSON.stringify({
      type: "permission_decision",
      request_id: permission.requestId,
      decision
    })
  );
  updatePermissionToolCard(permission, decision === "approved" ? "approved" : "denied");
  appendTimeline(
    permission.conversationId,
    decision === "approved" ? "Permission approved" : "Permission denied",
    permission.toolName,
    decision === "approved" ? "done" : "failed"
  );
  if (pendingPermission.value?.requestId === permission.requestId) pendingPermission.value = null;
}

async function stopRun() {
  if (activeConversationId.value) {
    updateConversationStatus(activeConversationId.value, "failed");
    appendTimeline(activeConversationId.value, "Run stopped", "Stopped by user", "failed");
  }
  if (activeRunId.value) {
    try {
      await api.cancelRun(activeRunId.value);
    } catch (error) {
      backendError.value = error instanceof Error ? error.message : String(error);
    }
  }
  // 保留 socket，等待后端返回可持久化的取消终态，避免历史里只剩半条用户消息。
}

// ---------- Scenario views ----------

function isTodayDate(iso: string | null | undefined) {
  if (!iso) {
    return false;
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return false;
  }
  return startOfLocalDay(date) === startOfLocalDay(new Date());
}

function isReminderDueToday(reminder: api.ReminderItem) {
  if (reminder.status === "done") {
    return isTodayDate(reminder.last_fired_at ?? reminder.updated_at);
  }
  if (reminder.schedule_type === "daily") {
    return reminder.status === "active";
  }
  return isTodayDate(reminder.due_at);
}

function reminderSortTime(reminder: api.ReminderItem) {
  if (reminder.due_at) {
    return new Date(reminder.due_at).getTime();
  }
  if (reminder.time_of_day) {
    const [hour = "0", minute = "0"] = reminder.time_of_day.split(":");
    return Number(hour) * 60 + Number(minute);
  }
  return Number.MAX_SAFE_INTEGER;
}

function reminderTimeLabel(reminder: api.ReminderItem) {
  if (reminder.due_at) {
    return formatTime(reminder.due_at);
  }
  return reminder.time_of_day ?? "今天";
}

function joinFilePath(base: string, name: string) {
  if (!base || base === "/") {
    return `/${name}`;
  }
  return `${base.replace(/\/+$/, "")}/${name}`;
}

async function loadFiles(path: string) {
  filesLoading.value = true;
  filesUnavailable.value = false;
  try {
    const result = await api.listFiles(path);
    filePath.value = result.path || "/";
    fileParent.value = result.parent;
    fileEntries.value = result.entries;
    selectedFile.value = null;
  } catch {
    fileEntries.value = [];
    selectedFile.value = null;
    filesUnavailable.value = true;
  } finally {
    filesLoading.value = false;
  }
}

async function openFileEntry(entry: api.FileEntry) {
  const path = joinFilePath(filePath.value, entry.name);
  if (entry.type === "dir") {
    await loadFiles(path);
    return;
  }
  filesLoading.value = true;
  filesUnavailable.value = false;
  previewError.value = "";
  try {
    selectedFile.value = await api.previewFile(path);
  } catch (error) {
    selectedFile.value = null;
    previewError.value = error instanceof Error ? error.message : "无法预览该文件";
  } finally {
    filesLoading.value = false;
  }
}

function previewKindLabel(kind: string): string {
  return { text: "文本", pdf: "PDF", docx: "Word", pptx: "PPT" }[kind] ?? kind;
}

async function openFilePreview(path: string): Promise<void> {
  previewModalError.value = "";
  previewModal.value = null;
  try {
    previewModal.value = await api.previewFile(path);
  } catch (error) {
    previewModalError.value = error instanceof Error ? error.message : "预览失败";
  }
}

function closeFilePreview(): void {
  previewModal.value = null;
  previewModalError.value = "";
}

function saveFileToWorkspace(preview: api.FilePreview): void {
  if (workspaceLibraryItems.value.some((item) => item.path === preview.path)) return;
  workspaceItems.value.unshift({
    id: `workspace-file-${Date.now()}`,
    kind: "file",
    label: "文件",
    title: preview.name,
    summary: `${previewKindLabel(preview.kind)} · ${formatBytes(preview.size)}`,
    content: preview.content.slice(0, 2000),
    path: preview.path,
    status: "ready",
    updated: "刚刚",
    savedAt: "刚刚",
  });
  workspaceSaveNotice.value = `“${preview.name}”已保存到工作区`;
}

async function loadTasks() {
  tasksLoading.value = true;
  tasksUnavailable.value = false;
  try {
    const result = await api.listTasks();
    tasks.value = result.tasks;
    const nextDrafts = Object.fromEntries(
      Object.entries(pendingTaskDraftsByConversation.value).map(([conversationId, drafts]) => [
        conversationId,
        drafts.filter((draft) => !tasks.value.some(
          (task) => task.source_session_id === conversationId &&
            task.title === draft.taskDraft?.title &&
            task.due_at === draft.taskDraft?.dueAt
        ))
      ])
    );
    pendingTaskDraftsByConversation.value = nextDrafts;
  } catch {
    tasks.value = [];
    tasksUnavailable.value = true;
  } finally {
    tasksLoading.value = false;
  }
}

async function refreshLiveSourcesAfterTool(toolName: string): Promise<void> {
  if (!hasBackendToken.value) return;
  const refreshers: Promise<unknown>[] = [];
  if (toolName.startsWith("task_")) refreshers.push(loadTasks());
  if (toolName.startsWith("reminder_")) refreshers.push(loadReminders());
  if (toolName.startsWith("knowledge_")) refreshers.push(loadDocuments());
  if (toolName.startsWith("artifact_")) refreshers.push(loadArtifacts());
  await Promise.allSettled(refreshers);
}

async function loadArtifacts(): Promise<void> {
  if (!hasBackendToken.value) {
    artifacts.value = [];
    return;
  }
  try {
    const result = await api.fetchArtifacts();
    artifacts.value = result.artifacts;
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function addTask() {
  const title = newTaskTitle.value.trim();
  if (!title) {
    return;
  }
  tasksLoading.value = true;
  tasksUnavailable.value = false;
  try {
    const task = await api.createTask({
      title,
      description: newTaskDescription.value.trim() || undefined,
      due_at: newTaskDueAt.value || undefined
    });
    tasks.value = [task, ...tasks.value];
    newTaskTitle.value = "";
    newTaskDescription.value = "";
    newTaskDueAt.value = "";
  } catch {
    tasksUnavailable.value = true;
  } finally {
    tasksLoading.value = false;
  }
}

async function setTaskStatus(task: api.TaskItem, status: api.TaskItem["status"]) {
  tasksUnavailable.value = false;
  try {
    const updated = await api.updateTask(task.id, { status });
    tasks.value = tasks.value.map((item) => (item.id === task.id ? updated : item));
    if (status === "done" && updated.reminder_id) {
      await completeReminder(updated.reminder_id);
    }
  } catch {
    tasksUnavailable.value = true;
  }
}

function replaceTask(updated: api.TaskItem): void {
  tasks.value = tasks.value.map((item) => (item.id === updated.id ? updated : item));
  syncTaskDetailDraft(updated);
}

async function syncLinkedTaskReminder(task: api.TaskItem, dueAt: string | null): Promise<void> {
  const reminder = task.reminder_id
    ? reminders.value.find((item) => item.id === task.reminder_id)
    : undefined;
  if (!reminder) return;
  const updated = await api.patchReminder(reminder.id, dueAt
    ? {
        title: task.title,
        body: task.description || undefined,
        due_at: dueAt
      }
    : { status: "paused" });
  reminders.value = reminders.value.map((item) => item.id === updated.id ? updated : item);
}

async function setQuickTaskDue(daysFromToday: number): Promise<void> {
  const task = activeWorkspaceTask.value;
  if (!task) return;
  const current = task.due_at ? new Date(task.due_at) : new Date();
  const due = new Date();
  due.setDate(due.getDate() + daysFromToday);
  due.setHours(task.due_at ? current.getHours() : 18, task.due_at ? current.getMinutes() : 0, 0, 0);
  try {
    const updated = await api.updateTask(task.id, { due_at: due.toISOString() });
    replaceTask(updated);
    await syncLinkedTaskReminder(updated, updated.due_at ?? null);
    taskDetailNotice.value = daysFromToday === 0 ? "已安排到今天" : "已安排到明天";
  } catch (error) {
    taskDetailNotice.value = error instanceof Error ? error.message : "调整日期失败";
  }
}

async function postponeTask(days: number): Promise<void> {
  const task = activeWorkspaceTask.value;
  if (!task) return;
  const due = task.due_at ? new Date(task.due_at) : new Date();
  if (!task.due_at) due.setHours(18, 0, 0, 0);
  due.setDate(due.getDate() + days);
  try {
    const updated = await api.updateTask(task.id, { due_at: due.toISOString() });
    replaceTask(updated);
    await syncLinkedTaskReminder(updated, updated.due_at ?? null);
    taskDetailNotice.value = `已顺延 ${days} 天`;
  } catch (error) {
    taskDetailNotice.value = error instanceof Error ? error.message : "顺延失败";
  }
}

async function clearTaskDue(): Promise<void> {
  const task = activeWorkspaceTask.value;
  if (!task) return;
  try {
    const updated = await api.updateTask(task.id, { due_at: null });
    replaceTask(updated);
    await syncLinkedTaskReminder(updated, null);
    taskDetailNotice.value = "已移除截止时间";
  } catch (error) {
    taskDetailNotice.value = error instanceof Error ? error.message : "移除日期失败";
  }
}

async function toggleWorkspaceTaskReminder(): Promise<void> {
  const task = activeWorkspaceTask.value;
  if (!task) return;
  const reminder = activeWorkspaceReminder.value;
  try {
    if (reminder) {
      const updatedReminder = await api.patchReminder(reminder.id, {
        status: reminder.status === "active" ? "paused" : "active"
      });
      reminders.value = reminders.value.map((item) => item.id === updatedReminder.id ? updatedReminder : item);
      taskDetailNotice.value = updatedReminder.status === "active" ? "提醒已开启" : "提醒已暂停";
      return;
    }
    if (!task.due_at) {
      taskDetailNotice.value = "请先设置截止时间，再开启提醒";
      return;
    }
    const created = await api.createReminder({
      title: task.title,
      body: task.description || undefined,
      schedule_type: "once",
      due_at: task.due_at,
      timezone: "Asia/Shanghai"
    });
    reminders.value = [created, ...reminders.value];
    const updatedTask = await api.updateTask(task.id, { reminder_id: created.id });
    replaceTask(updatedTask);
    if (activeWorkspaceItem.value) {
      activeWorkspaceItem.value = { ...activeWorkspaceItem.value, reminderId: created.id };
    }
    taskDetailNotice.value = "提醒已开启";
  } catch (error) {
    taskDetailNotice.value = error instanceof Error ? error.message : "更新提醒失败";
  }
}

async function saveWorkspaceTask(): Promise<void> {
  const item = activeWorkspaceItem.value;
  const task = item ? workspaceTask(item) : undefined;
  if (!task || !taskDetailDraft.value.title.trim()) return;
  tasksUnavailable.value = false;
  try {
    const updated = await api.updateTask(task.id, {
      title: taskDetailDraft.value.title.trim(),
      description: taskDetailDraft.value.description.trim(),
      due_at: taskDetailDraft.value.dueAt || undefined,
      category: taskDetailDraft.value.category.trim() || "个人",
      priority: taskDetailDraft.value.priority,
      next_action: taskDetailDraft.value.nextAction.trim()
    });
    replaceTask(updated);
    await syncLinkedTaskReminder(updated, updated.due_at ?? null);
    taskDetailNotice.value = "待办已更新";
  } catch (error) {
    tasksUnavailable.value = true;
    taskDetailNotice.value = error instanceof Error ? error.message : "更新待办失败";
  }
}

async function toggleTask(task: api.TaskItem) {
  await setTaskStatus(task, task.status === "done" ? "todo" : "done");
}

async function removeTask(taskId: string) {
  tasksUnavailable.value = false;
  try {
    const task = tasks.value.find((item) => item.id === taskId);
    if (task?.reminder_id) {
      await api.deleteReminder(task.reminder_id);
      reminders.value = reminders.value.filter((item) => item.id !== task.reminder_id);
    }
    await api.deleteTask(taskId);
    tasks.value = tasks.value.filter((task) => task.id !== taskId);
    if (activeWorkspaceItem.value?.externalRef === taskId) {
      activeWorkspaceItem.value = null;
      openWorkspace();
    }
  } catch {
    tasksUnavailable.value = true;
  }
}
// ---------- Knowledge ----------

async function loadDocuments() {
  if (!hasBackendToken.value) {
    return;
  }
  const { documents: items } = await api.fetchDocuments();
  documents.value = items;
}

async function loadCollections() {
  if (!hasBackendToken.value) {
    return;
  }
  const { collections: items } = await api.fetchCollections();
  collections.value = items;
}

function collectionName(collectionId: string | null) {
  return collections.value.find((collection) => collection.id === collectionId)?.name ?? "—";
}

function addKnowledgeFilesToWorkspace(files: File[]) {
  if (files.length === 0) return;
  const nextItems = files
    .map((file) => ({
      id: `workspace-kb-${file.name}-${file.lastModified}`,
      kind: "knowledge" as const,
      title: file.name,
      summary: "待索引为个人知识库资料，之后可在对话中通过 RAG 返回相关回答。",
      status: "working" as const,
      updated: "刚刚",
      savedAt: "刚刚",
      sourceCount: 0
    }))
    .filter((item) => !workspaceItems.value.some((current) => current.id === item.id));
  if (nextItems.length === 0) return;
  workspaceItems.value = [...nextItems, ...workspaceItems.value];
  workspaceCategory.value = "knowledge";
  workspaceSaveNotice.value = `${nextItems.length} 份资料已加入个人知识库`;
}

// 对话输入框的文档附件单独上传；每张卡片保留自己的上传状态，不会因为一项失败阻塞其余文件。
const DOCUMENT_EXTENSIONS = ["md", "markdown", "txt", "pdf", "docx"];
const MAX_PENDING_ATTACHMENTS = 5;
let attachmentToastTimer: number | null = null;

function documentExtension(filename: string) {
  const basename = filename.trim().replace(/[/\\]+$/, "").split(/[/\\]/).pop() ?? "";
  const dotIndex = basename.lastIndexOf(".");
  if (dotIndex <= 0 || dotIndex === basename.length - 1) return "file";
  const extension = basename.slice(dotIndex + 1).toLowerCase();
  return extension.length <= 10 ? extension : "file";
}

function documentIconLabel(filename: string) {
  const extension = documentExtension(filename);
  if (extension === "file") return "FILE";
  if (extension === "markdown") return "MD";
  return extension.toUpperCase().slice(0, 4);
}

function attachmentDisplayName(attachment: api.AttachmentItem) {
  const filename = attachment.filename.trim();
  if (!filename || (filename === attachment.id && documentExtension(filename) === "file")) {
    return "已上传附件";
  }
  return filename;
}

function attachmentIconKind(attachment: api.AttachmentItem) {
  if (attachment.mime === "inode/directory" || attachment.mime === "application/x-directory" || attachment.filename.endsWith("/")) {
    return "folder";
  }
  return "document";
}

function fileEntryIconKind(entry: api.FileEntry) {
  return entry.type === "dir" ? "folder" : "document";
}

function documentMime(file: File) {
  if (file.type) return file.type;
  const extension = documentExtension(file.name);
  if (extension === "pdf") return "application/pdf";
  if (extension === "docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  return extension === "txt" ? "text/plain" : "text/markdown";
}

function formatFileSize(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.ceil(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(size >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

function readyPendingAttachments() {
  return pendingAttachments.value.flatMap((item) => (item.status === "ready" && item.attachment ? [item.attachment] : []));
}

function showAttachmentToast(message: string) {
  attachmentToast.value = message;
  if (attachmentToastTimer) window.clearTimeout(attachmentToastTimer);
  attachmentToastTimer = window.setTimeout(() => {
    attachmentToast.value = "";
    attachmentToastTimer = null;
  }, 2800);
}

async function uploadPendingDocument(localId: string, file: File) {
  try {
    const { attachments } = await api.uploadDocuments([file]);
    const attachment = attachments[0];
    if (!attachment) throw new Error("服务没有返回附件信息");
    pendingAttachments.value = pendingAttachments.value.map((item) =>
      item.localId === localId ? { ...item, status: "ready", attachment } : item
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    pendingAttachments.value = pendingAttachments.value.map((item) =>
      item.localId === localId ? { ...item, status: "error", error: message } : item
    );
  }
}

function addPendingDocuments(files: File[]) {
  if (!hasBackendToken.value) {
    showAttachmentToast("连接本地 Agent 服务后才能上传文档附件");
    return;
  }
  const available = MAX_PENDING_ATTACHMENTS - pendingImages.value.length - pendingAttachments.value.length;
  if (available <= 0) {
    showAttachmentToast("最多同时附加 5 个文件");
    return;
  }
  const accepted = files.filter((file) => {
    if (!DOCUMENT_EXTENSIONS.includes(documentExtension(file.name))) {
      showAttachmentToast(`不支持的文件类型：${file.name}`);
      return false;
    }
    return true;
  });
  if (accepted.length > available) showAttachmentToast("最多同时附加 5 个文件");
  const selected = accepted.slice(0, available);
  for (const file of selected) {
    const localId = `pending-doc-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    pendingAttachments.value.push({
      localId,
      filename: file.name,
      mime: documentMime(file),
      size: file.size,
      status: "uploading"
    });
    void uploadPendingDocument(localId, file);
  }
}

function attachDocumentsToConversation(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  if (files.length > 0) addPendingDocuments(files);
}

function removePendingAttachment(localId: string) {
  pendingAttachments.value = pendingAttachments.value.filter((item) => item.localId !== localId);
}

async function uploadDocuments(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  if (files.length === 0) return;
  addKnowledgeFilesToWorkspace(files);
  if (input.dataset.destination === "workspace") {
    activeView.value = "workspace";
  }
  if (!hasBackendToken.value) {
    documents.value = [
      ...files.map((file) => ({
        id: `local-doc-${file.name}-${file.lastModified}`,
        name: file.name,
        type: file.name.split(".").pop()?.toLowerCase() || "file",
        size: file.size,
        collection_id: null,
        status: "queued" as const,
        chunk_count: 0,
        error: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
      })),
      ...documents.value
    ];
    backendError.value = "Connect the local agent service to index uploaded documents.";
    return;
  }
  for (const file of files) {
    try {
      await api.uploadDocument(file);
    } catch (error) {
      backendError.value = `${file.name}: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
  await loadDocuments();
}

async function removeDocument(documentId: string) {
  if (!hasBackendToken.value) {
    documents.value = documents.value.filter((document) => document.id !== documentId);
    return;
  }
  try {
    await api.deleteDocument(documentId);
    documents.value = documents.value.filter((document) => document.id !== documentId);
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

function documentType(document: api.KnowledgeDocument) {
  return document.type;
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function displayDate(iso: string) {
  const date = new Date(iso);
  const today = new Date();
  const dayDelta = Math.round((startOfLocalDay(today) - startOfLocalDay(date)) / 86_400_000);
  if (dayDelta === 0) {
    return "今天";
  }
  if (dayDelta === 1) {
    return "昨天";
  }
  if (date.getFullYear() !== today.getFullYear()) {
    return date.toLocaleDateString("zh-CN", { year: "numeric", month: "numeric", day: "numeric" });
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function messageRunId(metadata: Record<string, unknown>): string | undefined {
  const runId = metadata.run_id;
  return typeof runId === "string" && runId.trim() ? runId : undefined;
}

function formatMeta(metadata: Record<string, unknown>) {
  const attachments = metadata.attachments;
  const surface = metadata.surface === "pet" ? "来自桌宠" : "";
  if (Array.isArray(attachments) && attachments.length > 0) {
    const attachmentLabel = `${attachments.length} attachment${attachments.length > 1 ? "s" : ""}`;
    return [surface, attachmentLabel].filter(Boolean).join(" · ");
  }
  return surface || undefined;
}

function formatAttachments(metadata: Record<string, unknown>): api.AttachmentItem[] | undefined {
  const raw = metadata.attachments;
  if (!Array.isArray(raw)) {
    return undefined;
  }
  const items: api.AttachmentItem[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      items.push({ id: entry, filename: entry, mime: "application/octet-stream", size: 0 });
    } else if (entry && typeof entry === "object" && "id" in entry) {
      const record = entry as { id: string; filename?: string; mime?: string; size?: number };
      items.push({
        id: record.id,
        filename: record.filename ?? record.id,
        mime: record.mime ?? "application/octet-stream",
        size: record.size ?? 0
      });
    }
  }
  return items.length > 0 ? items : undefined;
}

// ---------- Settings ----------

async function loadRuntimePanel() {
  runtimePanelError.value = "";
  if (!hasBackendToken.value) {
    runtimeStatus.value = null;
    runtimeEvents.value = [];
    speechStatus.value = null;
    speechPreloadJobId.value = "";
    speechPreloadStatus.value = null;
    speechPreloadError.value = "";
    clearSpeechPreloadPoll();
    storageUsage.value = null;
    return;
  }
  const failures: string[] = [];
  const [statusResult, eventsResult, speechResult, storageResult] = await Promise.allSettled([
    api.fetchRuntimeStatus(),
    api.fetchRuntimeEvents(),
    api.fetchSpeechStatus(),
    api.fetchStorageUsage()
  ]);
  if (statusResult.status === "fulfilled") {
    applyRuntimeStatus(statusResult.value);
  } else {
    failures.push("runtime status");
  }
  if (eventsResult.status === "fulfilled") {
    runtimeEvents.value = [...eventsResult.value.events].reverse();
  } else {
    failures.push("runtime events");
  }
  if (speechResult.status === "fulfilled") {
    applySpeechStatus(speechResult.value);
  } else {
    failures.push("speech status");
  }
  if (storageResult.status === "fulfilled") {
    storageUsage.value = storageResult.value;
  } else {
    failures.push("storage usage");
  }
  runtimePanelError.value = failures.length
    ? `Waiting for backend: ${failures.join(", ")}.`
    : "";
}

async function loadProfile() {
  if (!hasBackendToken.value) {
    return;
  }
  profile.value = await api.fetchProfile();
}

async function saveProfile() {
  if (!hasBackendToken.value) {
    backendError.value = "Connect the local agent service to save profile settings.";
    return;
  }
  try {
    profile.value = await api.saveProfile(profile.value);
    profileSaved.value = true;
    setTimeout(() => (profileSaved.value = false), 2000);
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadModels() {
  if (!hasBackendToken.value) {
    return;
  }
  models.value = await api.fetchModels();
  await loadAvailableModels(models.value.provider);
}

async function loadConfiguredModels() {
  if (!hasBackendToken.value) {
    configuredModels.value = [];
    return;
  }
  const { models: items } = await api.fetchConfiguredModels();
  configuredModels.value = items;
}

function syncSelectedModel() {
  const active = configuredModels.value.find(
    (item) => item.provider === models.value.provider && item.model === models.value.model
  );
  selectedModel.value = active ? configuredModelKey(active) : "";
}

async function loadAvailableModels(provider = models.value.provider) {
  if (!hasBackendToken.value) {
    return;
  }
  availableModelsLoading.value = true;
  availableModelsError.value = "";
  try {
    const result = await api.fetchAvailableModels(provider);
    availableModels.value = result.models;
    availableModelsError.value = result.error ?? "";
    if (!modelCandidate.value || !result.models.includes(modelCandidate.value)) {
      modelCandidate.value = result.models[0] ?? "";
    }
  } catch (error) {
    availableModels.value = [];
    availableModelsError.value = error instanceof Error ? error.message : String(error);
  } finally {
    availableModelsLoading.value = false;
  }
}

function changeModelProvider() {
  modelCandidate.value = "";
  modelTestResult.value = "";
  void loadAvailableModels(models.value.provider);
}

async function saveModelsSettings() {
  if (!hasBackendToken.value) {
    backendError.value = "Connect the local agent service to save model settings.";
    return;
  }
  try {
    models.value = await api.saveModels(models.value);
    syncSelectedModel();
    await loadAvailableModels(models.value.provider);
    modelsSaved.value = true;
    setTimeout(() => (modelsSaved.value = false), 2000);
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function applySelectedModel(): Promise<void> {
  const selected = configuredModels.value.find(
    (item) => configuredModelKey(item) === selectedModel.value
  );
  if (!selected) return;
  if (selected.provider === models.value.provider && selected.model === models.value.model) return;
  models.value = { ...models.value, provider: selected.provider, model: selected.model };
  await saveModelsSettings();
}

async function testAndAddModel() {
  if (!modelCandidate.value || !hasBackendToken.value) {
    return;
  }
  modelTestLoading.value = true;
  modelTestResult.value = "Testing model...";
  try {
    const result = await api.testModel(models.value.provider, modelCandidate.value);
    modelTestResult.value = result.ok ? result.message : `Failed: ${result.message}`;
    if (!result.ok) return;
    await loadConfiguredModels();
    if (!selectedModel.value) {
      selectedModel.value = configuredModelKey({
        provider: result.provider,
        model: result.model
      });
      await applySelectedModel();
    }
  } catch (error) {
    modelTestResult.value = error instanceof Error ? error.message : String(error);
  } finally {
    modelTestLoading.value = false;
  }
}

async function removeConfiguredModel(item: api.ConfiguredModel) {
  try {
    await api.deleteConfiguredModel(item.provider, item.model);
    configuredModels.value = configuredModels.value.filter(
      (configured) => configuredModelKey(configured) !== configuredModelKey(item)
    );
    syncSelectedModel();
  } catch (error) {
    modelTestResult.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadSkills() {
  if (!hasBackendToken.value) {
    skills.value = [];
    return;
  }
  const { skills: items } = await api.fetchSkills();
  skills.value = items;
}

async function toggleSkill(name: string) {
  if (!hasBackendToken.value) {
    backendError.value = "Connect the local agent service to update skills.";
    return;
  }
  const skill = skills.value.find((item) => item.name === name);
  if (!skill) {
    return;
  }
  try {
    const updated = await api.patchSkill(name, !skill.enabled);
    skills.value = skills.value.map((item) => (item.name === name ? updated : item));
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

function skillImportErrorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  const normalized = message.toLowerCase();
  if (normalized.includes("duplicate") || normalized.includes("already") || normalized.includes("exists") || normalized.includes("conflict")) {
    return "导入失败：技能名称已存在";
  }
  if (normalized.includes("zip") || normalized.includes("archive") || normalized.includes("format")) {
    return "导入失败：Skill 包格式错误，请选择有效的 .zip 文件";
  }
  if (normalized.includes("manifest") || normalized.includes("validation") || normalized.includes("invalid")) {
    return "导入失败：Skill 包校验失败";
  }
  return `导入失败：${message}`;
}

function openSkillFilePicker() {
  skillImportInput.value?.click();
}

function toggleSkillPromptPreview(name: string) {
  expandedSkillNames.value = { ...expandedSkillNames.value, [name]: !expandedSkillNames.value[name] };
}

function showSkillPackageExample() {
  skillImportError.value = "";
  skillImportStatus.value = "Skill 包应为 .zip，内含 manifest 与对应的提示词或工具定义。";
}

function onSkillImportDragOver(event: DragEvent) {
  event.preventDefault();
  skillDropActive.value = true;
}

function onSkillImportDragLeave(event: DragEvent) {
  if (event.currentTarget === event.target) skillDropActive.value = false;
}

function onSkillImportDrop(event: DragEvent) {
  event.preventDefault();
  skillDropActive.value = false;
  const file = event.dataTransfer?.files?.[0];
  if (file) void importSkillFile(file);
}

async function importSkillFile(file: File) {
  skillImportStatus.value = "";
  skillImportError.value = "";
  if (!file.name.toLowerCase().endsWith(".zip")) {
    skillImportError.value = "导入失败：Skill 包格式错误，请选择 .zip 文件";
    return;
  }
  if (!hasBackendToken.value) {
    skillImportError.value = "连接本地 Agent 服务后才能导入 Skill Package。";
    return;
  }
  if (skillImporting.value) return;
  skillImporting.value = true;
  skillImportProgress.value = 12;
  if (skillImportProgressTimer) window.clearInterval(skillImportProgressTimer);
  skillImportProgressTimer = window.setInterval(() => {
    skillImportProgress.value = Math.min(88, skillImportProgress.value + 9);
  }, 260);
  try {
    const result = await api.importSkillPackage(file);
    await loadSkills();
    skillImportProgress.value = 100;
    skillImportStatus.value = `技能 ${result.skill.name} 已导入，默认关闭`;
  } catch (error) {
    skillImportError.value = skillImportErrorMessage(error);
  } finally {
    if (skillImportProgressTimer) window.clearInterval(skillImportProgressTimer);
    skillImportProgressTimer = null;
    skillImporting.value = false;
    window.setTimeout(() => {
      skillImportProgress.value = 0;
    }, 700);
  }
}

function importSkillPackage(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (file) void importSkillFile(file);
}

async function removeSkill(name: string) {
  const skill = skills.value.find((item) => item.name === name);
  if (!skill || skill.builtin || !hasBackendToken.value) return;
  try {
    await api.deleteSkill(name);
    skills.value = skills.value.filter((item) => item.name !== name);
    skillImportStatus.value = `已移除 ${name}`;
  } catch (error) {
    skillImportStatus.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadApiKeys() {
  if (!hasBackendToken.value) {
    return;
  }
  const { keys } = await api.fetchApiKeys();
  apiKeys.value = keys;
}

async function saveNewApiKey() {
  if (!apiKeyInput.value.trim()) {
    return;
  }
  if (!hasBackendToken.value) {
    backendError.value = "Connect the local agent service to save encrypted API keys.";
    return;
  }
  try {
    const record = await api.saveApiKey(apiKeyProvider.value, apiKeyInput.value.trim());
    apiKeys.value = [
      record,
      ...apiKeys.value.filter((item) => item.provider !== record.provider)
    ];
    apiKeyInput.value = "";
    apiKeyTestResult.value = "API Key 已加密保存；请测试连接或测试一个模型。";
    await loadAvailableModels(apiKeyProvider.value);
    apiKeySaved.value = true;
    setTimeout(() => (apiKeySaved.value = false), 2000);
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function removeApiKey(provider: string) {
  if (!hasBackendToken.value) {
    return;
  }
  try {
    await api.deleteApiKey(provider);
    apiKeys.value = apiKeys.value.filter((item) => item.provider !== provider);
  } catch (error) {
    backendError.value = error instanceof Error ? error.message : String(error);
  }
}

async function testApiKeyConnection() {
  apiKeyTestResult.value = "Testing...";
  if (!hasBackendToken.value) {
    apiKeyTestResult.value = "Connect the local agent service first.";
    return;
  }
  try {
    const result = await api.testApiKey(apiKeyProvider.value, apiKeyInput.value.trim() || undefined);
    if (result.ok && result.models) {
      availableModels.value = result.models;
      if (models.value.provider === result.provider && !modelCandidate.value) {
        modelCandidate.value = result.models[0] ?? "";
      }
    }
    apiKeyTestResult.value = result.ok
      ? `Connection OK (${result.provider})`
      : `Failed: ${result.message}`;
  } catch (error) {
    apiKeyTestResult.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadReminders() {
  remindersPanelError.value = "";
  if (!hasBackendToken.value) {
    reminders.value = [];
    return;
  }
  try {
    const { reminders: items } = await api.fetchReminders("all");
    reminders.value = items.filter((item) => item.status !== "deleted");
  } catch (error) {
    reminders.value = [];
    remindersPanelError.value =
      error instanceof Error ? `Waiting for backend: ${error.message}` : String(error);
  }
}

async function saveReminder() {
  if (!newReminder.value.title.trim()) {
    remindersPanelError.value = "Give the reminder a title first.";
    return;
  }
  if (!hasBackendToken.value) {
    remindersPanelError.value = "Connect the local agent service to save reminders.";
    return;
  }
  try {
    const reminder = await api.createReminder({
      ...newReminder.value,
      title: newReminder.value.title.trim(),
      body: newReminder.value.body?.trim() || undefined
    });
    reminders.value = [reminder, ...reminders.value];
    newReminder.value.title = "";
    newReminder.value.body = "";
    reminderSaved.value = true;
    remindersPanelError.value = "";
    setTimeout(() => (reminderSaved.value = false), 2000);
  } catch (error) {
    remindersPanelError.value = error instanceof Error ? error.message : String(error);
  }
}

async function completeReminder(reminderId: string) {
  try {
    const updated = await api.completeReminder(reminderId);
    reminders.value = reminders.value.map((item) => (item.id === reminderId ? updated : item));
  } catch (error) {
    remindersPanelError.value = error instanceof Error ? error.message : String(error);
  }
}

async function dismissReminder(reminderId: string) {
  try {
    const updated = await api.dismissReminder(reminderId);
    reminders.value = reminders.value.map((item) => (item.id === reminderId ? updated : item));
  } catch (error) {
    remindersPanelError.value = error instanceof Error ? error.message : String(error);
  }
}

async function deleteReminder(reminderId: string) {
  try {
    await api.deleteReminder(reminderId);
    reminders.value = reminders.value.filter((item) => item.id !== reminderId);
  } catch (error) {
    remindersPanelError.value = error instanceof Error ? error.message : String(error);
  }
}

function reminderScheduleLabel(reminder: api.ReminderItem) {
  if (reminder.schedule_type === "once") {
    return reminder.due_at ? new Date(reminder.due_at).toLocaleString() : "One-time reminder";
  }
  return `${reminder.schedule_type} at ${reminder.time_of_day ?? "09:30"}`;
}

function formatBytes(bytes: number) {
  if (bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

let activeMediaRecorder: MediaRecorder | null = null;
let activeMediaStream: MediaStream | null = null;
let recordedChunks: Blob[] = [];
let voiceInputTarget: "chat" | "artifact" | "pet" = "chat";
let voiceError = ref("");

function appendWorkspaceSystemMessage(text: string) {
  const item = activeWorkspaceItem.value;
  if (!item) return;
  workspaceEditMessages.value = {
    ...workspaceEditMessages.value,
    [item.id]: [
      ...activeWorkspaceEditMessages.value,
      { id: `workspace-system-${Date.now()}`, role: "system", text }
    ]
  };
}

async function startVoiceInput(target: "chat" | "artifact" | "pet" = "chat") {
  voiceInputTarget = target;
  if (isListening.value) {
    await stopVoiceInput();
    return;
  }
  if (!hasBackendToken.value || connectionStatus.value === "error") {
    if (voiceInputTarget === "artifact") {
      appendWorkspaceSystemMessage("语音识别需要连接本地 Agent 服务，请先配置后端。");
      return;
    }
    if (voiceInputTarget === "pet") {
      petVoiceState.value = "error";
      petError.value = "语音识别需要连接本地 Agent 服务。";
      schedulePetCollapse();
      return;
    }
    appendMessage(
      activeConversationId.value ?? createLocalConversation("Voice input"),
      "system",
      "语音识别需要连接本地 Agent 服务，请先配置后端。"
    );
    return;
  }
  voiceError.value = "";
  try {
    activeMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    voiceError.value = "无法访问麦克风，请在系统设置中允许 DeskBits 使用麦克风。";
    if (voiceInputTarget === "artifact") {
      appendWorkspaceSystemMessage(voiceError.value);
      return;
    }
    if (voiceInputTarget === "pet") {
      petVoiceState.value = "error";
      petError.value = voiceError.value;
      schedulePetCollapse();
      return;
    }
    appendMessage(
      activeConversationId.value ?? createLocalConversation("Voice input"),
      "system",
      voiceError.value
    );
    return;
  }
  recordedChunks = [];
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "";
  activeMediaRecorder = new MediaRecorder(activeMediaStream, mimeType ? { mimeType } : undefined);
  activeMediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) {
      recordedChunks.push(event.data);
    }
  };
  activeMediaRecorder.start();
  isListening.value = true;
  if (voiceInputTarget === "pet") {
    petVoiceState.value = "listening";
  }
  if (idlePetBubbleTimer) {
    window.clearTimeout(idlePetBubbleTimer);
    idlePetBubbleTimer = null;
  }
}

async function stopVoiceInput() {
  if (activeMediaRecorder && activeMediaRecorder.state !== "inactive") {
    const recorder = activeMediaRecorder;
    const stopped = new Promise<void>((resolve) => {
      recorder.addEventListener("stop", () => resolve(), { once: true });
    });
    recorder.stop();
    await stopped;
  }
  activeMediaRecorder = null;
  if (activeMediaStream) {
    activeMediaStream.getTracks().forEach((track) => track.stop());
    activeMediaStream = null;
  }
  isListening.value = false;
  scheduleIdlePetBubble();
  const mimeType = recordedChunks[0]?.type || "audio/webm";
  const blob = new Blob(recordedChunks, { type: mimeType });
  recordedChunks = [];
  if (discardRecordedVoice) {
    discardRecordedVoice = false;
    return;
  }
  if (blob.size === 0) {
    if (voiceInputTarget === "artifact") {
      appendWorkspaceSystemMessage("录音太短，请按住说话更久一些。");
      return;
    }
    if (voiceInputTarget === "pet") {
      petVoiceState.value = "error";
      petError.value = "录音太短，请按住说话更久一些。";
      schedulePetCollapse();
      return;
    }
    appendMessage(
      activeConversationId.value ?? createLocalConversation("Voice input"),
      "system",
      "录音太短，请按住说话更久一些。"
    );
    return;
  }
  try {
    if (voiceInputTarget === "pet") {
      petVoiceState.value = "transcribing";
    }
    const wavBlob = await convertToWav(blob);
    const result = await api.transcribeAudio(wavBlob);
    if (result.text) {
      if (voiceInputTarget === "artifact") {
        workspaceEditText.value = [workspaceEditText.value, result.text].filter(Boolean).join(" ");
      } else if (voiceInputTarget === "pet") {
        await sendPetCommand(result.text);
      } else {
        composerText.value = [composerText.value, result.text].filter(Boolean).join(" ");
        appendTimeline(
          activeConversationId.value ?? "",
          "Voice transcribed",
          result.text.slice(0, 40),
          "done"
        );
      }
    } else {
      if (voiceInputTarget === "artifact") {
        appendWorkspaceSystemMessage("未识别到语音，请重新录音。");
        return;
      }
      if (voiceInputTarget === "pet") {
        petVoiceState.value = "error";
        petError.value = "未识别到语音，请再说一次。";
        schedulePetCollapse();
        return;
      }
      appendMessage(
        activeConversationId.value ?? createLocalConversation("Voice input"),
        "system",
        "未识别到语音，请重新录音。"
      );
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (voiceInputTarget === "artifact") {
      appendWorkspaceSystemMessage(`语音识别失败：${message}`);
      return;
    }
    if (voiceInputTarget === "pet") {
      petVoiceState.value = "error";
      petError.value = `语音识别失败：${message}`;
      schedulePetCollapse();
      return;
    }
    appendMessage(
      activeConversationId.value ?? createLocalConversation("Voice input"),
      "system",
      `语音识别失败：${message}`
    );
  }
}

async function convertToWav(audioBlob: Blob): Promise<Blob> {
  const arrayBuffer = await audioBlob.arrayBuffer();
  const audioContext = new AudioContext();
  const decoded = await audioContext.decodeAudioData(arrayBuffer);
  await audioContext.close();

  const targetSampleRate = 16000;
  const channel = decoded.getChannelData(0);
  const ratio = decoded.sampleRate / targetSampleRate;
  const targetLength = Math.round(channel.length / ratio);
  const resampled = new Float32Array(targetLength);
  for (let i = 0; i < targetLength; i += 1) {
    const sourceIndex = Math.min(Math.floor(i * ratio), channel.length - 1);
    resampled[i] = channel[sourceIndex];
  }

  const buffer = new ArrayBuffer(44 + resampled.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + resampled.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, targetSampleRate, true);
  view.setUint32(28, targetSampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, resampled.length * 2, true);
  for (let i = 0; i < resampled.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, resampled[i]));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

// ---------- Image attachments ----------

const IMAGE_ACCEPT = ["image/png", "image/jpeg", "image/webp", "image/gif"];

function addPendingImages(files: FileList | File[]) {
  imageUploadError.value = "";
  const candidates = Array.from(files);
  let acceptedCount = 0;
  for (const file of candidates) {
    if (!IMAGE_ACCEPT.includes(file.type)) {
      imageUploadError.value = `不支持的文件类型：${file.name}（仅支持 PNG/JPEG/WebP/GIF）`;
      continue;
    }
    if (file.size > 10 * 1024 * 1024) {
      imageUploadError.value = `图片过大：${file.name}（单张不超过 10MB）`;
      continue;
    }
    if (pendingImages.value.length + pendingAttachments.value.length + acceptedCount >= MAX_PENDING_ATTACHMENTS) {
      showAttachmentToast("最多同时附加 5 个文件");
      break;
    }
    acceptedCount += 1;
    const reader = new FileReader();
    reader.onload = () => {
      pendingImages.value.push({
        file,
        dataUrl: String(reader.result)
      });
    };
    reader.readAsDataURL(file);
  }
}

function onImageFilesSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  if (input.files) {
    addPendingImages(input.files);
  }
  input.value = "";
}

function removePendingImage(index: number) {
  pendingImages.value.splice(index, 1);
}

function onComposerPaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items ?? [];
  const imageFiles: File[] = [];
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) {
        imageFiles.push(file);
      }
    }
  }
  if (imageFiles.length > 0) {
    event.preventDefault();
    addPendingImages(imageFiles);
  }
}

const attachmentUrlCache = ref<Record<string, string>>({});
let attachmentPreviewRequestId = 0;

function isImageAttachment(attachment: api.AttachmentItem) {
  return attachment.mime.startsWith("image/") && attachment.mime !== "image/*";
}

function attachmentPreviewMode(attachment: api.AttachmentItem): AttachmentPreviewMode {
  if (isImageAttachment(attachment)) return "image";
  if (attachment.mime === "application/pdf" || documentExtension(attachment.filename) === "pdf") return "pdf";
  if (attachment.mime.startsWith("text/") || ["md", "markdown", "txt"].includes(documentExtension(attachment.filename))) {
    return "text";
  }
  if (
    attachment.mime === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    documentExtension(attachment.filename) === "docx"
  ) {
    return "office";
  }
  return "file";
}

function attachmentPreviewLabel(mode: AttachmentPreviewMode): string {
  return { image: "图片", text: "文本", pdf: "PDF", office: "Word", file: "文件" }[mode];
}

function attachmentPreviewAccent(mode: AttachmentPreviewMode): string {
  return { image: "image", text: "text", pdf: "pdf", office: "docx", file: "file" }[mode];
}

async function loadAttachmentPreview(attachment: api.AttachmentItem, requestId: number): Promise<void> {
  const url = await api.attachmentUrl(attachment.id);
  const mode = attachmentPreviewMode(attachment);
  if (mode === "text") {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`附件预览失败（${response.status}）`);
    }
    const content = await response.text();
    if (requestId !== attachmentPreviewRequestId) return;
    attachmentPreview.value = {
      attachment,
      url,
      mode,
      content: content.slice(0, 100000),
      truncated: content.length > 100000,
      loading: false,
      error: ""
    };
    return;
  }
  if (requestId !== attachmentPreviewRequestId) return;
  attachmentPreview.value = {
    attachment,
    url,
    mode,
    loading: false,
    error: ""
  };
}

async function openAttachment(attachment: api.AttachmentItem) {
  const requestId = ++attachmentPreviewRequestId;
  attachmentPreview.value = {
    attachment,
    url: "",
    mode: attachmentPreviewMode(attachment),
    loading: true,
    error: ""
  };
  try {
    await loadAttachmentPreview(attachment, requestId);
    if (requestId !== attachmentPreviewRequestId) return;
  } catch (error) {
    if (requestId !== attachmentPreviewRequestId) return;
    attachmentPreview.value = {
      attachment,
      url: "",
      mode: attachmentPreviewMode(attachment),
      loading: false,
      error: error instanceof Error ? error.message : "无法打开附件"
    };
  }
}

function closeAttachmentPreview(): void {
  attachmentPreviewRequestId += 1;
  attachmentPreview.value = null;
}

async function openAttachmentInSystem(): Promise<void> {
  if (!attachmentPreview.value?.url) return;
  try {
    window.open(attachmentPreview.value.url, "_blank", "noopener,noreferrer");
  } catch {
    showAttachmentToast("无法打开附件");
  }
}

async function revealAttachmentInFinder(): Promise<void> {
  const attachment = attachmentPreview.value?.attachment;
  if (!attachment) return;
  if (!window.desktopMate?.showItemInFolder) {
    showAttachmentToast("桌面端才支持在 Finder 中显示");
    return;
  }
  try {
    const location = await api.attachmentLocation(attachment.id);
    const shown = await window.desktopMate.showItemInFolder(location.path);
    showAttachmentToast(shown ? "已在 Finder 中显示附件" : "附件文件不存在");
  } catch (error) {
    showAttachmentToast(error instanceof Error ? error.message : "无法在 Finder 中显示");
  }
}

function attachmentSrcSync(attachmentId: string): string {
  const cached = attachmentUrlCache.value[attachmentId];
  if (cached) {
    return cached;
  }
  void api.attachmentUrl(attachmentId).then((url) => {
    attachmentUrlCache.value = { ...attachmentUrlCache.value, [attachmentId]: url };
  });
  return "";
}
</script>
<template>
  <div
    v-if="isPetMode"
    class="pet-float-frame"
    aria-label="DeskBits floating pet"
  >
    <section v-if="petPanelVisible" class="pet-voice-panel" aria-live="polite">
      <div class="pet-voice-status">
        <span class="pet-voice-indicator" :class="petVoiceState" aria-hidden="true"></span>
        <span>{{ petStatusLabel }}</span>
        <button
          v-if="petVoiceState === 'speaking'"
          class="pet-voice-stop"
          type="button"
          aria-label="停止播报"
          title="停止播报"
          @click="stopPetSpeech"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <p v-if="petTranscript" class="pet-voice-transcript">{{ petTranscript }}</p>
      <p v-if="petReply && petVoiceState !== 'approval'" class="pet-voice-reply">{{ petReply }}</p>
      <p v-if="petError" class="pet-voice-error">{{ petError }}</p>
      <div v-if="petVoiceState === 'listening'" class="pet-listening-bars" aria-hidden="true">
        <i></i><i></i><i></i><i></i><i></i>
      </div>
      <div v-if="petPermission" class="pet-approval-card">
        <strong>{{ petPermission.toolName }}</strong>
        <p>{{ petPermission.summary }}</p>
        <div class="pet-approval-actions">
          <button type="button" @click="decidePetPermission('denied')">取消</button>
          <button class="primary" type="button" @click="decidePetPermission('approved')">确认</button>
        </div>
      </div>
    </section>
    <button
      v-if="petBubble"
      class="pet-bubble pet-float-bubble"
      type="button"
      :class="petBubble.kind"
      :aria-label="petBubble.label"
      :title="petBubble.label"
      @pointerdown="handlePetPointerDown"
      @pointermove="handlePetPointerMove"
      @pointerup="handlePetPointerUp"
      @pointercancel="handlePetPointerCancel"
      @click="handlePetClick"
      @dblclick="handlePetDoubleClick"
    >
      <span
        class="pet-bubble-sprite"
        :style="{
          backgroundImage: `url(${petBubbleSpriteUrl})`,
          backgroundPosition: petBubble.position
        }"
        aria-hidden="true"
      ></span>
    </button>
    <button
      class="pet-main-button pet-float-button"
      type="button"
      :aria-label="`单击与 ${selectedPetOption.label} 语音交互，再次单击结束语音输入，双击返回 DeskBits，按住拖动浮窗`"
      title="单击开始/结束语音输入，双击返回 DeskBits，按住拖动"
      @pointerdown="handlePetPointerDown"
      @pointermove="handlePetPointerMove"
      @pointerup="handlePetPointerUp"
      @pointercancel="handlePetPointerCancel"
      @click="handlePetClick"
      @dblclick="handlePetDoubleClick"
      @contextmenu.prevent
    >
      <span
        class="pet-sprite pet-float-sprite"
        :class="petMotionClass"
        :style="{
          backgroundImage: `url(${petSpriteUrl})`,
          backgroundPosition: selectedPetOption.position
        }"
        aria-hidden="true"
      ></span>
      <span class="pet-wave one" aria-hidden="true"></span>
      <span class="pet-wave two" aria-hidden="true"></span>
    </button>
  </div>
  <div
    v-else
    class="desktop-frame"
    :class="{ 'left-collapsed': !leftSidebarOpen, 'right-collapsed': !rightSidebarOpen }"
  >
    <aside class="sidebar">
      <div class="brand-row">
        <div class="brand-lockup">
          <img class="brand-logo" :src="deskbitsLogoUrl" alt="DeskBits" />
        </div>
      </div>

      <section class="sidebar-section">
        <button
          class="workspace-entry"
          :class="{ active: activeView === 'workspace' }"
          type="button"
          @click="openWorkspace"
        >
          <span><strong>工作区</strong><small>知识库与已保存成果</small></span>
          <em>{{ workspaceLibraryItems.length }}</em>
        </button>
      </section>

      <div class="sidebar-scroll-area">
        <section class="sidebar-section project-sidebar-section">
          <div class="section-heading">
            <div class="section-title">项目</div>
            <button class="new-chat-button project-add-button" type="button" @click="openAddProjectDialog">＋ 添加项目</button>
          </div>
          <div v-if="projectError" class="project-sidebar-error">{{ projectError }}</div>
          <div v-if="projects.length === 0" class="project-sidebar-empty">
            <span>📁</span>
            <small>添加项目文件夹后，对话会自动带上项目结构和 README。</small>
          </div>
          <div v-else class="project-sidebar-list">
            <article
              v-for="project in projects"
              :key="project.id"
              class="project-sidebar-card"
              :class="{ active: activeProjectId === project.id, missing: !project.exists, expanded: expandedProjectIds[project.id] }"
            >
              <div class="project-sidebar-main">
                <button class="project-sidebar-summary" type="button" :title="project.path" @click="toggleProject(project)">
                  <span class="project-folder-icon" aria-hidden="true">{{ expandedProjectIds[project.id] ? "▾" : "▸" }}</span>
                  <strong>{{ projectDirectoryName(project) }}</strong>
                </button>
                <div class="project-sidebar-actions">
                  <button
                    type="button"
                    :disabled="!project.exists"
                    :aria-label="`在 ${project.name} 中新建对话`"
                    title="新建项目对话"
                    @click.stop="startProjectSession(project)"
                  >
                    ＋
                  </button>
                  <button
                    class="project-sidebar-delete"
                    type="button"
                    :aria-label="`移除项目 ${project.name}`"
                    title="移除项目引用"
                    @click.stop="removeProject(project)"
                  >
                    ×
                  </button>
                </div>
              </div>
              <transition name="project-expand">
                <div v-if="expandedProjectIds[project.id]" class="project-sidebar-details">
                  <div v-if="projectSessionsLoading[project.id]" class="project-session-empty">读取项目对话…</div>
                  <button
                    v-else-if="projectSessions(project.id).length === 0"
                    class="project-session-row project-session-empty-action"
                    type="button"
                    :disabled="!project.exists"
                    @click="startProjectSession(project)"
                  >
                    还没有对话，点击新建
                  </button>
                  <button
                    v-for="session in projectSessions(project.id)"
                    :key="session.id"
                    class="project-session-row"
                    :class="{ active: activeConversationId === session.id }"
                    type="button"
                    @click="openProjectSession(project, session)"
                  >
                    <span class="project-session-branch" aria-hidden="true"></span>
                    <span>{{ session.title }}</span>
                  </button>
                </div>
              </transition>
            </article>
          </div>
        </section>

        <section class="sidebar-section">
          <div class="section-heading">
            <div class="section-title">对话历史</div>
            <div class="conversation-heading-actions">
              <button class="new-chat-button" type="button" @click="startNewChat">新对话</button>
              <button
                class="conversation-search-button"
                type="button"
                :class="{ active: conversationSearchOpen || conversationSearch }"
                :aria-expanded="conversationSearchOpen"
                aria-label="搜索对话"
                title="搜索对话"
                @click="conversationSearchOpen = !conversationSearchOpen"
              >
                ⌕
              </button>
            </div>
          </div>
          <input
            v-if="conversationSearchOpen || conversationSearch"
            v-model="conversationSearch"
            class="sidebar-search"
            aria-label="搜索对话"
            placeholder="搜索对话"
          />
          <div class="conversation-list">
            <div v-if="filteredConversations.length === 0" class="sidebar-empty">还没有对话</div>
            <div v-for="group in groupedConversations" :key="group.date" class="conversation-group">
              <div class="conversation-date-heading">{{ group.date }}</div>
              <div v-for="conversation in group.conversations" :key="conversation.id" class="conversation-row">
                <button
                  class="conversation-item"
                  :class="{ active: conversation.active && (activeView === 'chat' || activeView === 'project') }"
                  type="button"
                  @click="selectConversation(conversation.id)"
                >
                  <span class="item-dot" :class="conversation.status"></span>
                  <span class="item-title">{{ conversation.title }}</span>
                  <span class="item-date">{{ conversation.date }}</span>
                </button>
                <button
                  class="conversation-delete"
                  type="button"
                  :aria-label="`Delete ${conversation.title}`"
                  title="Delete conversation"
                  @click.stop="deleteConversation(conversation.id)"
                >
                  ×
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div class="sidebar-footer">
        <button
          class="settings-entry"
          type="button"
          :class="{ active: activeView === 'settings' }"
          @click="openSettings('profile')"
        >
          <span>Settings</span>
          <small>Profile, pet, skills, keys</small>
        </button>
        <div class="profile-chip">
          <span class="avatar">{{ userInitials }}</span>
          <span>
            <strong>{{ profile.display_name }}</strong>
            <small>Local-first workspace</small>
          </span>
        </div>
      </div>
    </aside>

    <button
      class="sidebar-edge-toggle"
      type="button"
      :aria-label="leftSidebarOpen ? '收起对话历史' : '展开对话历史'"
      :title="leftSidebarOpen ? '收起对话历史' : '展开对话历史'"
      @click="leftSidebarOpen = !leftSidebarOpen"
    >
      {{ leftSidebarOpen ? "‹" : "›" }}
    </button>

    <main class="workspace">
      <header class="topbar">
        <div>
          <h1>{{ currentTitle }}</h1>
          <p v-if="activeView === 'chat'">
            {{
              connectionStatus === "connected"
                ? `Connected to local agent runtime (${runtimeInfo?.version ?? ""}).`
                : connectionStatus === "error"
                  ? `Backend unreachable: ${backendError}`
                  : "Local draft mode until a backend token is configured."
            }}
          </p>
          <p v-else-if="activeView === 'today'">
            今日待办、最近会话和知识库状态。
          </p>
          <p v-else-if="activeView === 'files'">
            浏览本地文件；后端接口上线前保持只读预览占位。
          </p>
          <p v-else-if="activeView === 'tasks'">
            管理任务清单；后端接口上线前显示占位状态。
          </p>
          <p v-else-if="activeView === 'knowledge'">
            Track ingestion status, chunks, and citations.
          </p>
          <p v-else-if="activeView === 'workspace'">
            保存的文档、研究、个人知识库和任务都集中在这里。
          </p>
          <p v-else-if="activeView === 'project'">
            浏览项目文件、编辑文本文件，并让项目对话自动携带结构与 README 上下文。
          </p>
          <p v-else-if="activeView === 'artifact'">
            {{ activeWorkspaceItem?.kind === "task" ? "直接编辑任务属性、提醒和执行状态。" : "在右侧直接交代修改，修改记录会保存为一条完整对话。" }}
          </p>
          <p v-else>Control account, personalization, models, skills, keys, and local data.</p>
        </div>
        <div class="topbar-actions">
          <button
            class="navigation-button"
            type="button"
            aria-label="返回上一页"
            title="返回上一页"
            :disabled="!canNavigateBack"
            @click="navigateBack"
          >
            ←
          </button>
          <button
            class="navigation-button"
            type="button"
            aria-label="返回下一页"
            title="返回下一页"
            :disabled="!canNavigateForward"
            @click="navigateForward"
          >
            →
          </button>
          <button
            v-if="activeView === 'chat' || activeView === 'project'"
            class="side-action"
            type="button"
            :aria-label="rightSidebarOpen ? '收起右侧栏' : '展开右侧栏'"
            :title="rightSidebarOpen ? '收起右侧栏' : '展开右侧栏'"
            @click="rightSidebarOpen = !rightSidebarOpen"
          >
            {{ rightSidebarOpen ? "收起侧栏" : "展开侧栏" }}
          </button>
          <button v-if="runInProgress" class="stop-button" type="button" @click="stopRun">Stop</button>
        </div>
      </header>

      <section v-if="activeView === 'today'" class="scenario-layout today-layout">
        <div class="metric-grid today-metrics">
          <article>
            <strong>{{ todaysReminders.length }}</strong>
            <span>今日待办</span>
          </article>
          <article>
            <strong>{{ recentConversations.length }}</strong>
            <span>最近会话</span>
          </article>
          <article>
            <strong>{{ documents.length }}</strong>
            <span>知识库文档</span>
          </article>
        </div>
        <div class="scenario-grid">
          <section class="panel-card scenario-panel">
            <div class="scenario-panel-header">
              <h2>今日待办</h2>
              <span>{{ todaysReminders.length }}</span>
            </div>
            <div v-if="todaysReminders.length === 0" class="empty-panel compact">
              <h3>今天没有待办</h3>
              <p>提醒会在这里显示，方便快速查看当天安排。</p>
            </div>
            <div v-else class="compact-list">
              <div v-for="reminder in todaysReminders.slice(0, 5)" :key="reminder.id" class="compact-row">
                <span class="status-dot" :class="{ done: reminder.status === 'done' }"></span>
                <span>
                  <strong>{{ reminder.title }}</strong>
                  <small>{{ reminderTimeLabel(reminder) }} · {{ reminder.status === "done" ? "已完成" : "待处理" }}</small>
                </span>
              </div>
            </div>
          </section>

          <section class="panel-card scenario-panel">
            <div class="scenario-panel-header">
              <h2>最近会话</h2>
              <span>{{ recentConversations.length }}</span>
            </div>
            <div v-if="recentConversations.length === 0" class="empty-panel compact">
              <h3>还没有会话</h3>
              <p>开始对话后，最近记录会显示在这里。</p>
            </div>
            <div v-else class="compact-list">
              <button
                v-for="conversation in recentConversations"
                :key="conversation.id"
                class="compact-row clickable"
                type="button"
                @click="selectConversation(conversation.id)"
              >
                <span class="item-dot" :class="conversation.status"></span>
                <span>
                  <strong>{{ conversation.title }}</strong>
                  <small>{{ conversation.date }}</small>
                </span>
              </button>
            </div>
          </section>

          <section class="panel-card scenario-panel knowledge-summary-panel">
            <div class="scenario-panel-header">
              <h2>知识库</h2>
              <span>{{ documents.length }}</span>
            </div>
            <div class="settings-facts compact-facts">
              <span>已索引</span>
              <strong>{{ indexedDocumentCount }}</strong>
              <span>待处理</span>
              <strong>{{ documents.filter((document) => document.status === "queued" || document.status === "parsing").length }}</strong>
              <span>需检查</span>
              <strong>{{ failedDocumentCount }}</strong>
            </div>
            <button class="save-button" type="button" @click="activeView = 'knowledge'">打开知识库</button>
          </section>
        </div>
      </section>

      <section v-else-if="activeView === 'project' && !activeProject" class="project-workbench">
        <div class="empty-panel project-empty-state">
          <h3>还没有选中项目</h3>
          <p>从左侧添加项目，展开后选择项目对话或新建对话。</p>
          <button class="save-button" type="button" @click="openAddProjectDialog">添加项目</button>
        </div>
      </section>

      <section v-else-if="activeView === 'workspace'" class="workspace-library">
        <div class="workspace-library-header">
          <div>
            <h2>工作区</h2>
            <p>把对话中确认保存的成果和知识库资料放在一起，随时带回新的工作。</p>
          </div>
          <div class="workspace-library-actions">
            <button class="workspace-upload-button project-folder-button" type="button" @click="toggleProjectContextPanel">
              <span>{{ projectContextOpen ? "收起项目文件夹" : "打开项目文件夹" }}</span>
            </button>
            <label class="workspace-upload-button">
              <input
                type="file"
                multiple
                accept=".md,.markdown,.txt,.pdf,.docx"
                data-destination="workspace"
                @change="uploadDocuments"
              />
              <span>上传资料</span>
            </label>
            <input v-model="workspaceSearch" aria-label="搜索工作区" placeholder="搜索工作区" />
          </div>
        </div>
        <div class="workspace-window" aria-label="工作区分类滑动窗口">
          <button
            v-for="category in workspaceCategories"
            :key="category.id"
            class="workspace-category"
            :class="{ active: workspaceCategory === category.id }"
            type="button"
            @click="workspaceCategory = category.id"
          >
            <span>{{ category.label }}</span><small>{{ category.count }}</small>
          </button>
        </div>
        <div v-if="workspaceSaveNotice" class="workspace-notice">{{ workspaceSaveNotice }}</div>
        <section v-if="projectContextOpen" class="workspace-project-panel panel-card" aria-label="项目文件夹上下文">
          <div class="project-context-head">
            <div>
              <span class="output-kind file">项目概览</span>
              <h3>选择一个项目文件夹，生成结构 + README 上下文</h3>
              <p>生成后会作为 <code>workspace_context</code> 带入下一条消息，适合让 Agent 快速理解项目。</p>
            </div>
            <button class="side-action" type="button" :disabled="fsRootsLoading" @click="loadFsRoots">
              {{ fsRootsLoading ? "刷新中" : "刷新根目录" }}
            </button>
          </div>
          <div class="project-context-layout">
            <div class="project-tree-pane">
              <div v-if="fsRootsLoading && fsRoots.length === 0" class="project-tree-empty">正在读取可访问目录…</div>
              <div v-else-if="fsRoots.length === 0" class="project-tree-empty">暂无可访问目录，请检查后端 allowed_roots 配置。</div>
              <div v-else class="project-tree-list">
                <div
                  v-for="row in visibleFsRows"
                  :key="`${row.type}-${row.path}`"
                  class="project-tree-row"
                  :class="{ selected: row.path === fsSelectedPath, file: row.type === 'file', missing: row.exists === false }"
                  :style="{ paddingLeft: `${10 + row.depth * 18}px` }"
                >
                  <button
                    v-if="fsRowIsDirectory(row)"
                    class="project-tree-toggle"
                    type="button"
                    :aria-label="fsExpandedPaths[row.path] ? '折叠目录' : '展开目录'"
                    :disabled="row.exists === false || fsTreeLoadingPath === row.path"
                    @click="toggleFsDirectory(row)"
                  >
                    <span v-if="fsTreeLoadingPath === row.path" class="mini-spinner" aria-hidden="true"></span>
                    <span v-else>{{ fsExpandedPaths[row.path] ? "⌄" : "›" }}</span>
                  </button>
                  <span v-else class="project-tree-spacer"></span>
                  <button
                    class="project-tree-name"
                    type="button"
                    :disabled="row.type === 'file' || row.exists === false"
                    :title="row.path"
                    @click="selectFsDirectory(row)"
                  >
                    <span class="project-tree-icon" :class="row.type" aria-hidden="true">{{ row.type === "file" ? "□" : "▰" }}</span>
                    <span>{{ row.name }}</span>
                    <small v-if="row.type === 'file'">{{ formatBytes(row.size || 0) }}</small>
                    <small v-else-if="row.exists === false">不存在</small>
                  </button>
                </div>
              </div>
            </div>
            <div class="project-context-preview">
              <div class="project-context-preview-head">
                <div>
                  <span>当前选择</span>
                  <strong>{{ fsSelectedPath || "尚未选择目录" }}</strong>
                </div>
                <button class="save-button" type="button" :disabled="!fsSelectedPath || fsContextLoading" @click="generateFsContext()">
                  {{ fsContextLoading ? "生成中" : "生成上下文" }}
                </button>
              </div>
              <p v-if="fsContextError" class="project-context-error">{{ fsContextError }}</p>
              <template v-if="fsContext">
                <div class="project-context-stats">
                  <span><strong>{{ fsContext.total_files }}</strong> files</span>
                  <span v-for="ext in fsContext.extensions" :key="ext.ext">{{ ext.ext }} × {{ ext.count }}</span>
                </div>
                <div class="project-context-readme">
                  <strong>README</strong>
                  <p>{{ fsContext.readme || "未找到 README。" }}</p>
                </div>
                <pre class="project-context-tree">{{ fsContext.tree || "没有可展示的目录结构。" }}</pre>
                <div class="project-context-actions">
                  <span>已准备作为下一条消息的项目上下文。</span>
                  <button type="button" @click="askWithProjectContext">去对话提问</button>
                </div>
              </template>
              <div v-else class="project-context-empty">
                <strong>先选择目录，再生成项目概览</strong>
                <p>DeskBits 会提取目录结构、README 和主要文件类型，不读取大体量源码正文。</p>
              </div>
            </div>
          </div>
        </section>
        <section v-if="workspaceCategory === 'task'" class="workspace-task-board">
          <div class="task-board-summary">
            <span><strong>{{ workspaceTaskItems.length }}</strong> 项待办</span>
            <small>事项、截止时间、提醒计划与完成状态都会保存在这里。</small>
          </div>
          <div class="task-board-columns">
            <article>
              <span>待办</span>
              <strong>{{ workspaceTaskItems.filter((item) => workspaceTaskStatus(item) === 'todo').length }}</strong>
            </article>
            <article>
              <span>进行中</span>
              <strong>{{ workspaceTaskItems.filter((item) => workspaceTaskStatus(item) === 'in_progress').length }}</strong>
            </article>
            <article>
              <span>已完成</span>
              <strong>{{ workspaceTaskItems.filter((item) => workspaceTaskStatus(item) === 'done').length }}</strong>
            </article>
          </div>
        </section>
        <div v-if="filteredWorkspaceItems.length === 0" class="empty-panel workspace-empty">
          <h3>这里还没有匹配的内容</h3>
          <p>在对话里让 Agent 完成任务，确认保存后成果会出现在这里。</p>
        </div>
        <div v-else class="workspace-grid">
          <article v-for="item in filteredWorkspaceItems" :key="item.id" class="workspace-card sticky-note" :class="`note-${item.kind}`">
            <div class="workspace-card-top">
              <span class="output-kind" :class="item.kind">{{ outputKindLabel(item.kind, item.label) }}</span>
              <small>{{ item.savedAt }}</small>
            </div>
            <h3>{{ item.title }}</h3>
            <p>{{ item.summary }}</p>
            <div class="workspace-card-footer">
              <span v-if="item.kind === 'task'">{{ taskStatusLabel(workspaceTaskStatus(item)) }}</span>
              <span v-else>{{ item.sourceCount ? `${item.sourceCount} 项资料` : "已保存成果" }}</span>
              <button v-if="item.kind === 'file' && item.path" type="button" @click="openFilePreview(item.path!)">预览</button>
              <button type="button" @click="openWorkspaceItem(item)">打开</button>
              <button type="button" @click="openConversationPicker(item)">添加到对话</button>
            </div>
          </article>
        </div>
      </section>

      <section
        v-else-if="activeView === 'artifact' && activeWorkspaceItem"
        class="artifact-workbench"
        :class="{ 'task-only': activeWorkspaceItem.kind === 'task' }"
      >
        <section class="artifact-canvas-wrap">
          <div class="artifact-workbench-bar">
            <button type="button" class="artifact-back" @click="openWorkspace">‹</button>
            <span class="output-kind" :class="activeWorkspaceItem.kind">{{ outputKindLabel(activeWorkspaceItem.kind, activeWorkspaceItem.label) }}</span>
            <span>已保存到工作区</span>
            <small>{{ activeWorkspaceItem.updated }}</small>
          </div>
          <article
            v-if="activeWorkspaceItem.kind === 'task' && activeWorkspaceTask"
            class="task-workspace-view"
          >
            <header class="task-focus-header">
              <button
                class="task-complete-toggle"
                :class="{ completed: activeWorkspaceTask.status === 'done' }"
                type="button"
                :aria-label="activeWorkspaceTask.status === 'done' ? '重新打开待办' : '完成待办'"
                @click="toggleTask(activeWorkspaceTask)"
              ><span></span></button>
              <div>
                <p>{{ taskDetailDraft.category || "个人" }} · {{ taskStatusLabel(activeWorkspaceTask.status) }}</p>
                <input v-model="taskDetailDraft.title" class="task-title-input" aria-label="待办标题" />
              </div>
              <button class="task-save-command" type="button" @click="saveWorkspaceTask">保存</button>
            </header>

            <div class="task-focus-layout">
              <section class="task-main-content">
                <label class="task-content-field">
                  <span>任务说明</span>
                  <textarea v-model="taskDetailDraft.description" rows="5" placeholder="补充背景、完成标准或注意事项"></textarea>
                </label>
                <label class="task-content-field task-next-action-field">
                  <span>下一步行动</span>
                  <input v-model="taskDetailDraft.nextAction" placeholder="写下一步可以立刻开始的动作" />
                </label>
                <div class="task-progress-section">
                  <div><span>任务进度</span><small>直接切换当前执行状态</small></div>
                  <div class="task-status-switch">
                    <button type="button" :class="{ active: activeWorkspaceTask.status === 'todo' }" @click="setTaskStatus(activeWorkspaceTask, 'todo')">待办</button>
                    <button type="button" :class="{ active: activeWorkspaceTask.status === 'in_progress' }" @click="setTaskStatus(activeWorkspaceTask, 'in_progress')">进行中</button>
                    <button type="button" :class="{ active: activeWorkspaceTask.status === 'done' }" @click="setTaskStatus(activeWorkspaceTask, 'done')">已完成</button>
                  </div>
                </div>
              </section>

              <aside class="task-property-panel">
                <section class="task-property-group">
                  <div class="task-property-heading"><span>截止时间</span><strong>{{ taskDueLabel(activeWorkspaceTask.due_at) }}</strong></div>
                  <input v-model="taskDetailDraft.dueAt" type="datetime-local" aria-label="截止时间" />
                  <div class="task-quick-actions">
                    <button type="button" @click="setQuickTaskDue(0)">今天</button>
                    <button type="button" @click="setQuickTaskDue(1)">明天</button>
                    <button type="button" @click="postponeTask(1)">顺延一天</button>
                    <button type="button" @click="clearTaskDue">无日期</button>
                  </div>
                </section>
                <section class="task-property-group">
                  <div class="task-property-heading"><span>提醒</span><strong>{{ activeWorkspaceReminder?.status === "active" ? "已开启" : activeWorkspaceReminder ? "已暂停" : "未设置" }}</strong></div>
                  <p>{{ activeWorkspaceReminder ? reminderScheduleLabel(activeWorkspaceReminder) : "开启后会在截止时间发送本地提醒。" }}</p>
                  <button class="task-property-action" type="button" @click="toggleWorkspaceTaskReminder">{{ activeWorkspaceReminder?.status === "active" ? "暂停提醒" : "开启提醒" }}</button>
                </section>
                <section class="task-property-group task-property-grid">
                  <label><span>分类</span><input v-model="taskDetailDraft.category" /></label>
                  <label><span>优先级</span><select v-model="taskDetailDraft.priority"><option value="low">低</option><option value="normal">普通</option><option value="high">高</option></select></label>
                </section>
                <section class="task-meta-row">
                  <span>创建于 {{ new Date(activeWorkspaceTask.created_at).toLocaleDateString("zh-CN") }}</span>
                  <button type="button" @click="removeTask(activeWorkspaceTask.id)">删除待办</button>
                </section>
              </aside>
            </div>
            <p v-if="taskDetailNotice" class="task-detail-notice">{{ taskDetailNotice }}</p>
          </article>
          <article
            v-else-if="activeWorkspaceItem.kind === 'task' && workspaceReminder(activeWorkspaceItem)"
            class="artifact-document workbench-document reminder-artifact-view"
          >
            <p class="artifact-overline">提醒</p>
            <h2>{{ workspaceReminder(activeWorkspaceItem)?.title }}</h2>
            <p class="artifact-lead">{{ workspaceReminder(activeWorkspaceItem)?.body || "这是一条由 DeskBits 创建的提醒。" }}</p>
            <div class="artifact-fact-grid">
              <span>时间<strong>{{ reminderScheduleLabel(workspaceReminder(activeWorkspaceItem)!) }}</strong></span>
              <span>状态<strong>{{ workspaceReminder(activeWorkspaceItem)!.status === "active" ? "已启用" : "已完成" }}</strong></span>
            </div>
            <div class="artifact-action-row">
              <button type="button" class="artifact-primary" @click="completeReminder(workspaceReminder(activeWorkspaceItem)!.id)">完成提醒</button>
            </div>
          </article>
          <article v-else class="artifact-document workbench-document">
            <p class="artifact-overline">{{ activeWorkspaceItem.title }}</p>
            <h2>{{ workspaceDocumentTitle(activeWorkspaceItem) }}</h2>
            <p class="artifact-lead">{{ activeWorkspaceItem.summary }}</p>
            <section class="artifact-reading-content">
              <template v-for="(block, index) in artifactContentBlocks(activeWorkspaceItem.content, activeWorkspaceItem.summary)" :key="`${block.type}-${index}`">
                <h3 v-if="block.type === 'heading'">{{ block.text }}</h3>
                <ul v-else-if="block.type === 'bullet_list'">
                  <li v-for="item in block.items" :key="item">{{ item }}</li>
                </ul>
                <ol v-else-if="block.type === 'ordered_list'">
                  <li v-for="item in block.items" :key="item">{{ item }}</li>
                </ol>
                <p v-else>{{ block.text }}</p>
              </template>
            </section>
          </article>
        </section>
        <aside v-if="activeWorkspaceItem.kind !== 'task'" class="artifact-chat-panel" aria-label="修改对话">
          <div class="artifact-chat-header">
            <div><span>修改对话</span><strong>继续完善</strong></div>
            <small>{{ activeWorkspaceItem.conversationId ? "已保存到对话历史" : "首次发送后保存" }}</small>
          </div>
          <div class="artifact-chat-messages">
            <article v-for="message in activeWorkspaceEditMessages" :key="message.id" :class="message.role">
              <span>{{ message.role === "user" ? "你" : message.role === "system" ? "系统" : "DeskBits" }}</span>
              <p>{{ message.text }}</p>
              <div v-if="message.attachments?.some(isImageAttachment)" class="message-images artifact-message-images">
                <button
                  v-for="attachment in message.attachments?.filter(isImageAttachment)"
                  :key="attachment.id"
                  class="message-image-button"
                  type="button"
                  :title="`打开 ${attachment.filename}`"
                  @click="openAttachment(attachment)"
                >
                  <img class="message-image" :src="attachmentSrcSync(attachment.id)" :alt="attachment.filename" loading="lazy" />
                </button>
              </div>
              <div v-if="message.attachments?.some((attachment) => !isImageAttachment(attachment))" class="message-document-attachments">
                <button
                  v-for="attachment in message.attachments?.filter((attachment) => !isImageAttachment(attachment))"
                  :key="attachment.id"
                  type="button"
                  class="message-document-chip"
                  :class="`type-${documentExtension(attachment.filename)}`"
                  :title="`打开 ${attachment.filename}`"
                  @click="openAttachment(attachment)"
                >
                  <span class="attachment-file-icon" :class="`icon-${attachmentIconKind(attachment)}`" aria-hidden="true">
                    <svg v-if="attachmentIconKind(attachment) === 'folder'" class="attachment-file-svg" viewBox="0 0 24 24">
                      <path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.15c.6 0 1.16.24 1.58.66l1.09 1.09H18A2.25 2.25 0 0 1 20.25 9v7.25A2.75 2.75 0 0 1 17.5 19H6.5a2.75 2.75 0 0 1-2.75-2.75v-9Z" />
                      <path d="M4.25 9.25h15.5" />
                    </svg>
                    <svg v-else class="attachment-file-svg" viewBox="0 0 24 24">
                      <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                      <path d="M13.5 4v4.75h4.75" />
                    </svg>
                    <b>{{ documentIconLabel(attachment.filename) }}</b>
                  </span>
                  <span>{{ attachmentDisplayName(attachment) }}</span>
                  <span aria-hidden="true">↗</span>
                </button>
              </div>
            </article>
          </div>
          <form class="composer artifact-chat-composer" @submit.prevent="sendWorkspaceEdit" @paste="onComposerPaste">
            <p v-if="attachmentToast" class="composer-toast" role="status">{{ attachmentToast }}</p>
            <div v-if="pendingImages.length > 0" class="composer-preview composer-image-grid">
              <div v-for="(image, index) in pendingImages" :key="image.dataUrl" class="composer-preview-item">
                <img :src="image.dataUrl" :alt="image.file.name" />
                <button type="button" :aria-label="`Remove image ${index + 1}`" @click="removePendingImage(index)">
                  ×
                </button>
              </div>
            </div>
            <section v-if="pendingAttachments.length > 0" class="composer-document-list" aria-label="待发送文档">
              <article
                v-for="attachment in pendingAttachments"
                :key="attachment.localId"
                class="composer-attachment-card"
                :class="[`is-${attachment.status}`, `type-${documentExtension(attachment.filename)}`]"
                :title="attachment.filename"
              >
                <span class="attachment-file-icon icon-document" aria-hidden="true">
                  <svg class="attachment-file-svg" viewBox="0 0 24 24">
                    <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                    <path d="M13.5 4v4.75h4.75" />
                  </svg>
                  <b>{{ documentIconLabel(attachment.filename) }}</b>
                </span>
                <span class="composer-attachment-copy">
                  <strong>{{ attachment.filename }}</strong>
                  <small v-if="attachment.status === 'uploading'">正在上传</small>
                  <small v-else-if="attachment.status === 'error'" class="attachment-error">{{ attachment.error || "上传失败" }}</small>
                  <small v-else>{{ formatFileSize(attachment.size) }}</small>
                </span>
                <span v-if="attachment.status === 'uploading'" class="attachment-spinner" aria-label="上传中"></span>
                <button type="button" :aria-label="`移除附件 ${attachment.filename}`" @click="removePendingAttachment(attachment.localId)">×</button>
              </article>
            </section>
            <div class="composer-toolbar artifact-composer-toolbar">
              <label class="composer-document-button" title="上传文档附件（md/txt/pdf/docx）">
                <input type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx" @change="attachDocumentsToConversation" />
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m20.5 11.5-8.9 8.9a5 5 0 0 1-7.1-7.1l9.2-9.2a3.5 3.5 0 0 1 5 5l-9.2 9.2a2 2 0 1 1-2.8-2.8l8.5-8.5" /></svg>
              </label>
              <label class="composer-image-button" title="上传图片附件">
                <input type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" @change="onImageFilesSelected" />
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <rect x="3" y="4.5" width="18" height="15" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.7" />
                  <circle cx="8.5" cy="10" r="1.8" fill="currentColor" />
                  <path d="M4.5 17.5 9.5 12.5l3.5 3.5 3-3 3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </label>
              <select v-model="selectedModel" aria-label="Model" :disabled="composerModelOptions.length === 0" @change="applySelectedModel">
                <option value="" disabled>Configure a verified model</option>
                <option v-for="model in composerModelOptions" :key="configuredModelKey(model)" :value="configuredModelKey(model)">
                  {{ model.provider }} / {{ model.model }}
                </option>
              </select>
            </div>
            <div class="composer-input-row artifact-composer-input-row">
              <button
                class="voice-button"
                type="button"
                :class="{ listening: isListening }"
                :aria-label="isListening ? 'Listening' : 'Voice input'"
                :title="isListening ? 'Listening' : 'Voice input'"
                @click="startVoiceInput('artifact')"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path
                    d="M12 14.5a3.5 3.5 0 0 0 3.5-3.5V6a3.5 3.5 0 0 0-7 0v5a3.5 3.5 0 0 0 3.5 3.5Z"
                  />
                  <path d="M5.75 10.75a6.25 6.25 0 0 0 12.5 0" />
                  <path d="M12 17v3.25" />
                  <path d="M8.75 20.25h6.5" />
                </svg>
              </button>
              <textarea
                ref="artifactComposerInput"
                v-model="workspaceEditText"
                aria-label="修改成品"
                placeholder="交代修改要求，或让 Agent 继续处理"
                rows="1"
                @input="handleComposerInput"
                @keydown="handleComposerKeydown"
              ></textarea>
              <button
                class="send-button"
                type="submit"
                aria-label="发送修改"
                :disabled="(!workspaceEditText.trim() && pendingImages.length === 0 && readyPendingAttachments().length === 0) || pendingDocumentUploading"
              >
                ↗
              </button>
            </div>
            <div
              v-if="speechPreloadHint"
              class="voice-model-hint"
              :class="speechPreloadHint.kind"
            >
              <span>{{ speechPreloadHint.text }}</span>
              <button
                v-if="speechPreloadHint.kind === 'missing' || speechPreloadHint.kind === 'failed'"
                type="button"
                :disabled="isSpeechPreloadActive || speechPreloadPolling"
                @click="startSpeechPreload"
              >
                {{ speechPreloadHint.kind === "failed" ? "重试" : "下载" }}
              </button>
            </div>
          </form>
          <p class="artifact-chat-hint">发送后会创建候选新版本；确认后才会替换当前内容。</p>
        </aside>
      </section>

      <section v-else-if="activeView === 'files'" class="scenario-layout">
        <div class="panel-header">
          <div>
            <h2>文件</h2>
            <p>只读浏览和预览，后端文件接口上线后自动可用。</p>
          </div>
          <button class="side-action" type="button" :disabled="filesLoading" @click="loadFiles(filePath)">
            {{ filesLoading ? "加载中" : "刷新" }}
          </button>
        </div>
        <div class="file-browser">
          <section class="panel-card file-list-panel">
            <nav class="breadcrumb" aria-label="File path">
              <button
                v-for="crumb in fileBreadcrumbs"
                :key="crumb.path"
                type="button"
                @click="loadFiles(crumb.path)"
              >
                {{ crumb.label }}
              </button>
            </nav>
            <button
              v-if="fileParent"
              class="file-row"
              type="button"
              @click="loadFiles(fileParent)"
            >
              <span class="file-icon icon-folder" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M11 7.25 7.75 4 4.5 7.25M7.75 4v15.5" />
                </svg>
              </span>
              <span>
                <strong>上一级</strong>
                <small>{{ fileParent }}</small>
              </span>
              <em></em>
            </button>
            <div v-if="filesUnavailable" class="empty-panel compact">
              <h3>文件浏览即将上线</h3>
              <p>当前后端还没有提供文件列表接口。</p>
            </div>
            <div v-else-if="!filesLoading && sortedFileEntries.length === 0" class="empty-panel compact">
              <h3>暂无文件</h3>
              <p>连接后端后会显示当前目录内容。</p>
            </div>
            <template v-else>
              <button
                v-for="entry in sortedFileEntries"
                :key="`${entry.type}-${entry.name}`"
                class="file-row"
                type="button"
                @click="openFileEntry(entry)"
              >
                <span class="file-icon" :class="`icon-${fileEntryIconKind(entry)}`" aria-hidden="true">
                  <svg v-if="fileEntryIconKind(entry) === 'folder'" viewBox="0 0 24 24">
                    <path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.15c.6 0 1.16.24 1.58.66l1.09 1.09H18A2.25 2.25 0 0 1 20.25 9v7.25A2.75 2.75 0 0 1 17.5 19H6.5a2.75 2.75 0 0 1-2.75-2.75v-9Z" />
                    <path d="M4.25 9.25h15.5" />
                  </svg>
                  <svg v-else viewBox="0 0 24 24">
                    <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                    <path d="M13.5 4v4.75h4.75" />
                  </svg>
                </span>
                <span>
                  <strong>{{ entry.name }}</strong>
                  <small>{{ displayDate(entry.modified_at) }}</small>
                </span>
                <em>{{ entry.type === "dir" ? "文件夹" : formatBytes(entry.size) }}</em>
              </button>
            </template>
          </section>
          <section class="panel-card file-preview-panel">
            <div v-if="selectedFile">
              <div class="scenario-panel-header">
                <h2>{{ selectedFile.path.split("/").pop() || selectedFile.path }}</h2>
                <div class="file-preview-meta">
                  <span class="output-kind" :class="selectedFile.kind">{{ previewKindLabel(selectedFile.kind) }}</span>
                  <span>{{ formatBytes(selectedFile.size) }}</span>
                  <button class="save-button" type="button" @click="saveFileToWorkspace(selectedFile)">保存到工作台</button>
                </div>
              </div>
              <p v-if="selectedFile.truncated" class="preview-truncated">内容过长，已截断前 10 万字符</p>
              <pre>{{ selectedFile.content }}</pre>
            </div>
            <div v-else-if="previewError" class="empty-panel compact">
              <h3>无法预览</h3>
              <p>{{ previewError }}</p>
            </div>
            <div v-else class="empty-panel compact">
              <h3>选择文件预览</h3>
              <p>点击文件后会在这里显示内容（支持 md/txt/PDF/DOCX/PPTX）。</p>
            </div>
          </section>
        </div>
      </section>

      <section v-else-if="activeView === 'tasks'" class="scenario-layout">
        <div class="panel-header">
          <div>
            <h2>任务</h2>
            <p>与后端任务参数对齐：标题、说明、截止时间和状态。</p>
          </div>
          <button class="side-action" type="button" :disabled="tasksLoading" @click="loadTasks">
            {{ tasksLoading ? "加载中" : "刷新" }}
          </button>
        </div>
        <form class="task-create-row" @submit.prevent="addTask">
          <input v-model="newTaskTitle" aria-label="New task title" placeholder="标题，例如：整理会议纪要" />
          <input v-model="newTaskDescription" aria-label="Task description" placeholder="说明，例如：提取结论、风险和下一步" />
          <input v-model="newTaskDueAt" aria-label="Task due time" type="datetime-local" />
          <button class="save-button" type="submit" :disabled="tasksLoading">添加</button>
        </form>
        <div v-if="tasksUnavailable" class="empty-panel">
          <h3>任务接口暂不可用</h3>
          <p>前端已经按 /api/tasks 的 title、description、due_at、status 参数发送。</p>
        </div>
        <div v-else-if="tasks.length === 0" class="empty-panel">
          <h3>暂无任务</h3>
          <p>添加任务后会显示在这里。</p>
        </div>
        <div v-else class="task-list">
          <div v-for="task in tasks" :key="task.id" class="task-row" :class="{ done: task.status === 'done' }">
            <label class="task-check">
              <input type="checkbox" :checked="task.status === 'done'" @change="toggleTask(task)" />
              <span></span>
            </label>
            <span>
              <strong>{{ task.title }}</strong>
              <small v-if="task.description">{{ task.description }}</small>
              <small>{{ task.due_at ? new Date(task.due_at).toLocaleString("zh-CN") : displayDate(task.created_at) }} · {{ taskStatusLabel(task.status) }}</small>
            </span>
            <div class="task-row-actions">
              <button
                class="row-action"
                type="button"
                :class="{ active: task.status === 'todo' }"
                @click="setTaskStatus(task, 'todo')"
              >
                待办
              </button>
              <button
                class="row-action"
                type="button"
                :class="{ active: task.status === 'in_progress' }"
                @click="setTaskStatus(task, 'in_progress')"
              >
                进行中
              </button>
              <button class="row-action" type="button" @click="removeTask(task.id)">删除</button>
            </div>
          </div>
        </div>
      </section>

      <section
        v-else-if="activeView === 'chat' || activeView === 'project'"
        class="chat-workspace"
        :class="{ 'right-panel-collapsed': !rightSidebarOpen, 'project-chat-workspace': activeView === 'project' }"
      >
        <section class="chat-main">
          <header v-if="activeView === 'project' && activeProject" class="project-workbench-header project-chat-header">
            <div>
              <span class="project-kicker">Project workspace</span>
              <h2>{{ activeProject.name }}</h2>
              <p :title="activeProject.path">{{ activeProject.path }}</p>
            </div>
            <div class="project-workbench-actions">
              <span v-if="!activeProject.exists" class="project-missing-badge">目录不存在</span>
              <span class="project-file-count"><strong>{{ activeProjectFileCount }}</strong> 文件</span>
              <button type="button" @click="startProjectSession(activeProject)">新建对话</button>
              <button type="button" class="primary" @click="refreshProjectFiles(activeProject)">刷新文件树</button>
            </div>
          </header>
          <section v-if="activeArtifact" class="artifact-preview">
            <div class="artifact-preview-bar">
              <button type="button" class="artifact-back" @click="activeArtifact = null">‹</button>
              <span class="output-kind" :class="activeArtifact.kind">{{ outputKindLabel(activeArtifact.kind, activeArtifact.label) }}</span>
              <span>来自当前对话</span>
              <div>
                <button v-if="activeArtifact.artifactId" type="button" @click="continueArtifactRevision">继续让 Agent 修改</button>
                <button v-if="activeArtifact.artifactId || activeArtifact.taskDraft" type="button" class="save-artifact-button" @click="requestSaveArtifact">保存到工作区</button>
              </div>
            </div>
            <article v-if="activeArtifact.kind === 'task' && activeArtifact.taskDraft" class="artifact-document task-candidate-preview">
              <div class="task-candidate-heading">
                <p class="artifact-overline">待办事项</p>
                <span class="task-candidate-state">等待保存</span>
              </div>
              <h2>{{ activeArtifact.taskDraft.title }}</h2>
              <p class="artifact-lead">{{ activeArtifact.taskDraft.description || "这项待办暂时没有补充说明。" }}</p>
              <div class="artifact-fact-grid task-candidate-facts">
                <span>状态<strong>待办</strong></span>
                <span>截止<strong>{{ activeArtifact.taskDraft.dueAt ? new Date(activeArtifact.taskDraft.dueAt).toLocaleString("zh-CN") : "未设置" }}</strong></span>
                <span>提醒<strong>{{ activeArtifact.taskDraft.reminder ? taskDraftScheduleLabel(activeArtifact.taskDraft) : "未设置" }}</strong></span>
                <span>分类<strong>{{ activeArtifact.taskDraft.category }}</strong></span>
                <span>优先级<strong>{{ taskPriorityLabel(activeArtifact.taskDraft.priority) }}</strong></span>
              </div>
              <section v-if="activeArtifact.taskDraft.nextAction" class="task-candidate-note">
                <strong>建议下一步</strong>
                <p>{{ activeArtifact.taskDraft.nextAction }}</p>
              </section>
              <section class="task-candidate-note">
                <strong>保存后</strong>
                <p>这项待办会进入工作区；{{ activeArtifact.taskDraft.reminder?.message || "到期时会按上面的时间发送本地提醒。" }} 同时会写入本次对话记忆。</p>
              </section>
            </article>
            <article v-else class="artifact-document">
              <p class="artifact-overline">{{ activeArtifact.title }}</p>
              <h2>{{ activeArtifact.title }}</h2>
              <p class="artifact-lead">{{ activeArtifact.summary }}</p>
              <section v-if="activeArtifact.content" class="artifact-reading-content">
                <template v-for="(block, index) in artifactContentBlocks(activeArtifact.content, activeArtifact.summary)" :key="`${block.type}-${index}`">
                  <h3 v-if="block.type === 'heading'">{{ block.text }}</h3>
                  <ul v-else-if="block.type === 'bullet_list'">
                    <li v-for="item in block.items" :key="item">{{ item }}</li>
                  </ul>
                  <ol v-else-if="block.type === 'ordered_list'">
                    <li v-for="item in block.items" :key="item">{{ item }}</li>
                  </ol>
                  <p v-else>{{ block.text }}</p>
                </template>
              </section>
              <template v-else>
                <section><h3>等待结果内容</h3><p>Agent 正在生成这份候选成品。内容返回后会在这里显示，确认保存前不会写入工作区。</p></section>
              </template>
            </article>
          </section>

          <div v-else ref="messageList" class="message-list">
            <div v-if="chatMessages.length === 0" class="chat-empty-state">
              <h2>{{ activeView === "project" && activeProject ? `开始和 ${activeProject.name} 对话` : "今天想让 DeskBits 帮你做什么？" }}</h2>
              <p>{{ activeView === "project" ? "项目结构和 README 会自动随消息带入，可以直接问这个项目是做什么的，或让 Agent 帮你定位文件。" : "可以直接提问、制定计划，或交代一项具体任务。" }}</p>
              <div class="suggestion-row">
                <button type="button" @click="composerText = '总结一下当前项目进度'">
                  总结项目进度
                </button>
                <button type="button" @click="composerText = '给我制定本周的工作计划'">
                  制定本周计划
                </button>
                <button type="button" @click="activeView = 'knowledge'">查看知识库</button>
              </div>
            </div>

            <template v-else>
              <div
                v-for="message in chatMessages"
                :key="message.id"
                class="message"
                :class="{
                  'user-message': message.role === 'user',
                  'agent-message': message.role === 'assistant',
                  'system-message': message.role === 'system',
                  'tool-message': message.role === 'tool'
                }"
              >
	                <div class="message-meta">
	                  <span>
	                    {{
	                      message.role === "user"
	                        ? "You"
	                        : message.role === "tool"
	                          ? "Tool"
	                          : message.role === "system"
	                            ? "Status"
	                            : "DeskBits"
	                    }}
	                  </span>
	                  <span v-if="activeConversationProject" class="message-project-badge">📁 {{ activeConversationProject.name }}</span>
	                </div>
                <section v-if="message.reasoning" class="thinking-panel" :class="{ expanded: isThinkingExpanded(message) }">
                  <button
                    type="button"
                    class="thinking-panel-toggle"
                    :aria-expanded="isThinkingExpanded(message)"
                    :aria-controls="`thinking-${message.id}`"
                    @click="toggleThinkingPanel(message)"
                  >
                    <span class="thinking-panel-chevron" aria-hidden="true"></span>
                    <span>{{ isThinkingExpanded(message) ? "收起思考过程" : "查看思考过程" }}</span>
                  </button>
                  <pre v-if="isThinkingExpanded(message)" :id="`thinking-${message.id}`" class="thinking-panel-content">{{ message.reasoning }}</pre>
                </section>
                <p v-if="message.text">{{ message.text }}</p>
                <div v-if="message.attachments?.some(isImageAttachment)" class="message-images">
                  <button
                    v-for="attachment in message.attachments?.filter(isImageAttachment)"
                    :key="attachment.id"
                    class="message-image-button"
                    type="button"
                    :title="`打开 ${attachment.filename}`"
                    @click="openAttachment(attachment)"
                  >
                    <img
                      class="message-image"
                      :src="attachmentSrcSync(attachment.id)"
                      :alt="attachment.filename"
                      loading="lazy"
                    />
                  </button>
                </div>
                <div v-if="message.attachments?.some((attachment) => !isImageAttachment(attachment))" class="message-document-attachments">
                  <button
                    v-for="attachment in message.attachments?.filter((attachment) => !isImageAttachment(attachment))"
                    :key="attachment.id"
                    type="button"
                    class="message-document-chip"
                    :class="`type-${documentExtension(attachment.filename)}`"
                    :title="`打开 ${attachment.filename}`"
                    @click="openAttachment(attachment)"
                  >
                    <span class="attachment-file-icon" :class="`icon-${attachmentIconKind(attachment)}`" aria-hidden="true">
                      <svg v-if="attachmentIconKind(attachment) === 'folder'" class="attachment-file-svg" viewBox="0 0 24 24">
                        <path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.15c.6 0 1.16.24 1.58.66l1.09 1.09H18A2.25 2.25 0 0 1 20.25 9v7.25A2.75 2.75 0 0 1 17.5 19H6.5a2.75 2.75 0 0 1-2.75-2.75v-9Z" />
                        <path d="M4.25 9.25h15.5" />
                      </svg>
                      <svg v-else class="attachment-file-svg" viewBox="0 0 24 24">
                        <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                        <path d="M13.5 4v4.75h4.75" />
                      </svg>
                      <b>{{ documentIconLabel(attachment.filename) }}</b>
                    </span>
                    <span>{{ attachmentDisplayName(attachment) }}</span>
                    <span aria-hidden="true">↗</span>
                  </button>
                </div>
                <span v-if="message.id.startsWith('stream-') && message.text" class="stream-cursor"></span>
                <small v-if="message.meta">{{ message.meta }}</small>

                <section
                  v-if="toolCardsForMessage(message).length > 0"
                  class="tool-call-ledger inline-tool-call-ledger"
                  aria-label="工具调用记录"
                >
                  <article
                    v-for="card in toolCardsForMessage(message)"
                    :key="card.id"
                    class="tool-call-card compact"
                    :class="`is-${card.status}`"
                  >
                    <header class="tool-call-header">
                      <div class="tool-call-title">
                        <span class="tool-call-icon" aria-hidden="true"></span>
                        <div>
                          <strong>{{ toolLabel(card.toolName) }}</strong>
                          <small>{{ card.toolName }}</small>
                        </div>
                      </div>
                      <div class="tool-call-badges">
                        <span class="tool-risk-badge" :class="`risk-${card.riskLevel}`">{{ toolRiskLabel(card.riskLevel) }}</span>
                        <span class="tool-status-badge" :class="`status-${card.status}`">{{ toolCardStatusLabel(card.status) }}</span>
                      </div>
                    </header>

                    <p class="tool-call-scope">{{ card.summary || toolCardScope(card) }}</p>

                    <div v-if="compactToolCardEntries(card.args).length > 0" class="tool-data-grid compact-grid">
                      <span v-for="entry in compactToolCardEntries(card.args)" :key="entry.key">
                        <b>{{ entry.key }}</b>
                        <i>{{ entry.value }}</i>
                      </span>
                    </div>

                    <section v-if="card.review" class="tool-review-snapshot">
                      <div class="tool-review-heading">
                        <strong>审核快照</strong>
                        <span v-if="card.review.ready === false">暂不可执行</span>
                        <span v-else>批准后将按此内容执行</span>
                      </div>
                      <p v-if="typeof card.review.message === 'string'" class="tool-review-message">{{ card.review.message }}</p>
                      <p v-if="typeof card.review.path === 'string'" class="tool-review-path">{{ card.review.path }}</p>
                      <pre v-if="typeof card.review.diff === 'string' && card.review.diff" class="tool-review-diff">{{ card.review.diff }}</pre>
                      <p v-else-if="card.review.changed === false" class="tool-review-message">内容没有变化。</p>
                    </section>

                    <details v-if="card.result" class="tool-result-details">
                      <summary>查看返回数据</summary>
                      <div class="tool-data-grid result-grid">
                        <span v-for="entry in toolCardEntries(card.result)" :key="entry.key">
                          <b>{{ entry.key }}</b>
                          <i>{{ entry.value }}</i>
                        </span>
                      </div>
                    </details>

                    <section v-if="card.status === 'waiting_approval' && pendingPermission?.toolCardId === card.id" class="tool-approval-panel">
                      <div>
                        <strong>需要人工确认</strong>
                        <p>{{ toolRiskDescription(card.riskLevel) }}</p>
                        <small>仅批准本次操作，不会获得长期权限。</small>
                      </div>
                      <div class="tool-approval-actions">
                        <button type="button" @click="pendingPermission && decidePermission(pendingPermission, 'denied')">拒绝</button>
                        <button class="primary" type="button" @click="pendingPermission && decidePermission(pendingPermission, 'approved')">允许本次操作</button>
                      </div>
                    </section>
                  </article>
                </section>
              </div>
            </template>

            <div v-if="activeCitations.length > 0" class="citation-card">
              <div class="citation-card-header">
                <span>来源</span>
                <span class="citation-count">{{ activeCitations.length }}</span>
              </div>
              <button
                v-for="citation in activeCitations"
                :key="citation.document_id || citation.url || citation.chunk_id || citation.source_path || citation.title"
                type="button"
                class="citation-row"
                @click="openCitationSource(citation)"
              >
                <span class="citation-title-wrap">
                  <span class="citation-index" aria-hidden="true">{{ activeCitations.indexOf(citation) + 1 }}</span>
                  <span class="citation-title">{{ citationDisplayName(citation) }}</span>
                </span>
                <span class="citation-source-type">{{ citation.source_type === "web" ? "网页" : "知识库" }}</span>
                <span class="citation-open">查看<span aria-hidden="true">↗</span></span>
              </button>
            </div>

            <section v-if="activeTaskDrafts.length > 0" class="task-draft-list" aria-label="待办候选">
              <article
                v-for="draftOutput in activeTaskDrafts"
                :key="draftOutput.id"
                class="task-confirm-card sticky-note note-task"
                :class="`priority-${draftOutput.taskDraft?.priority || 'normal'}`"
              >
                <div class="task-confirm-heading">
                  <span class="task-confirm-mark"><i></i>待办候选</span>
                  <span>{{ draftOutput.taskDraft?.priority === "high" ? "重要" : "等待确认" }}</span>
                </div>
                <div class="task-confirm-body">
                  <span class="task-check-preview" aria-hidden="true"></span>
                  <div>
                    <strong>{{ draftOutput.taskDraft?.title }}</strong>
                    <p v-if="draftOutput.taskDraft">{{ taskDraftMainText(draftOutput.taskDraft) }}</p>
                  </div>
                </div>
                <div class="task-confirm-meta">
                  <span class="task-note-date">{{ draftOutput.taskDraft ? taskDraftScheduleLabel(draftOutput.taskDraft) : "未设置时间" }}</span>
                  <span v-if="draftOutput.taskDraft?.reminder">会提醒</span>
                  <span>{{ draftOutput.taskDraft?.category || "个人" }}</span>
                </div>
                <div class="task-confirm-actions">
                  <button class="primary" type="button" @click="requestTaskDraftConfirmation(draftOutput)">确认加入待办</button>
                  <button type="button" @click="dismissTaskDraft(draftOutput)">暂不保存</button>
                </div>
              </article>
            </section>

            <div v-if="isWaitingForFirstResponse" class="thinking-indicator" aria-live="polite">
              <span>{{ answerProgressLabel }}</span>
              <span class="thinking-dots" aria-hidden="true">
                <span></span>
                <span></span>
                <span></span>
              </span>
              <span class="sr-only">正在思考...</span>
            </div>
          </div>

          <form class="composer" @submit.prevent="sendMessage" @paste="onComposerPaste">
            <p v-if="attachmentToast" class="composer-toast" role="status">{{ attachmentToast }}</p>
            <div v-if="pendingImages.length > 0" class="composer-preview composer-image-grid">
              <div v-for="(image, index) in pendingImages" :key="image.dataUrl" class="composer-preview-item">
                <img :src="image.dataUrl" :alt="image.file.name" />
                <button type="button" :aria-label="`Remove image ${index + 1}`" @click="removePendingImage(index)">
                  ×
                </button>
              </div>
            </div>
            <section v-if="pendingAttachments.length > 0" class="composer-document-list" aria-label="待发送文档">
              <article
                v-for="attachment in pendingAttachments"
                :key="attachment.localId"
                class="composer-attachment-card"
                :class="[`is-${attachment.status}`, `type-${documentExtension(attachment.filename)}`]"
                :title="attachment.filename"
              >
                <span class="attachment-file-icon icon-document" aria-hidden="true">
                  <svg class="attachment-file-svg" viewBox="0 0 24 24">
                    <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                    <path d="M13.5 4v4.75h4.75" />
                  </svg>
                  <b>{{ documentIconLabel(attachment.filename) }}</b>
                </span>
                <span class="composer-attachment-copy">
                  <strong>{{ attachment.filename }}</strong>
                  <small v-if="attachment.status === 'uploading'">正在上传</small>
                  <small v-else-if="attachment.status === 'error'" class="attachment-error">{{ attachment.error || "上传失败" }}</small>
                  <small v-else>{{ formatFileSize(attachment.size) }}</small>
                </span>
                <span v-if="attachment.status === 'uploading'" class="attachment-spinner" aria-label="上传中"></span>
                <button type="button" :aria-label="`移除附件 ${attachment.filename}`" @click="removePendingAttachment(attachment.localId)">×</button>
              </article>
            </section>
            <div class="composer-toolbar">
              <label class="composer-document-button" title="上传文档附件（md/txt/pdf/docx）">
                <input type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx" @change="attachDocumentsToConversation" />
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m20.5 11.5-8.9 8.9a5 5 0 0 1-7.1-7.1l9.2-9.2a3.5 3.5 0 0 1 5 5l-9.2 9.2a2 2 0 1 1-2.8-2.8l8.5-8.5" /></svg>
              </label>
              <label class="composer-image-button" title="上传图片附件">
                <input type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" @change="onImageFilesSelected" />
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <rect x="3" y="4.5" width="18" height="15" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.7" />
                  <circle cx="8.5" cy="10" r="1.8" fill="currentColor" />
                  <path d="M4.5 17.5 9.5 12.5l3.5 3.5 3-3 3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </label>
              <select v-model="selectedModel" aria-label="Model" :disabled="composerModelOptions.length === 0" @change="applySelectedModel">
                <option value="" disabled>Configure a verified model</option>
                <option v-for="model in composerModelOptions" :key="configuredModelKey(model)" :value="configuredModelKey(model)">
                  {{ model.provider }} / {{ model.model }}
                </option>
              </select>
            </div>
            <div class="composer-input-row">
              <button
                class="voice-button"
                type="button"
                :class="{ listening: isListening }"
                :aria-label="isListening ? 'Listening' : 'Voice input'"
                :title="isListening ? 'Listening' : 'Voice input'"
                @click="startVoiceInput()"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path
                    d="M12 14.5a3.5 3.5 0 0 0 3.5-3.5V6a3.5 3.5 0 0 0-7 0v5a3.5 3.5 0 0 0 3.5 3.5Z"
                  />
                  <path d="M5.75 10.75a6.25 6.25 0 0 0 12.5 0" />
                  <path d="M12 17v3.25" />
                  <path d="M8.75 20.25h6.5" />
                </svg>
              </button>
              <textarea
                ref="composerInput"
                v-model="composerText"
                aria-label="Message"
                :placeholder="composerPlaceholder"
                rows="1"
                @input="handleComposerInput"
                @keydown="handleComposerKeydown"
              ></textarea>
              <button
                class="send-button"
                type="submit"
                aria-label="Send message"
                :disabled="runInProgress || pendingDocumentUploading"
              >
                ↗
              </button>
            </div>
            <p v-if="pendingWorkspaceContext" class="composer-context-note">
              已添加参考资料：{{ pendingWorkspaceContext.title }}
            </p>
            <div
              v-if="speechPreloadHint"
              class="voice-model-hint"
              :class="speechPreloadHint.kind"
            >
              <span>{{ speechPreloadHint.text }}</span>
              <button
                v-if="speechPreloadHint.kind === 'missing' || speechPreloadHint.kind === 'failed'"
                type="button"
                :disabled="isSpeechPreloadActive || speechPreloadPolling"
                @click="startSpeechPreload"
              >
                {{ speechPreloadHint.kind === "failed" ? "重试" : "下载" }}
              </button>
            </div>
          </form>
        </section>
        <aside v-if="rightSidebarOpen" class="run-panel result-panel">
          <section v-if="activeView === 'project' && activeProject" class="result-sidebar-section project-file-sidebar-section">
            <div class="result-panel-header project-file-sidebar-header">
              <div><span>项目文件</span><strong>{{ activeProjectFileCount }}</strong></div>
              <button type="button" :disabled="projectFileLoadingPath === activeProject.path" @click="refreshProjectFiles(activeProject)">
                {{ projectFileLoadingPath === activeProject.path ? "刷新中" : "刷新" }}
              </button>
            </div>
            <div v-if="projectFileError" class="project-file-error compact">{{ projectFileError }}</div>
            <div v-if="projectFileNotice" class="project-file-notice compact">{{ projectFileNotice }}</div>
            <div v-if="projectFileLoadingPath === activeProject.path && visibleProjectFileRows.length <= 1" class="project-tree-empty">
              正在读取项目目录…
            </div>
            <div v-else class="project-tree-list project-sidebar-file-tree" aria-label="项目文件树">
              <div
                v-for="row in visibleProjectFileRows"
                :key="`${row.type}-${row.path}`"
                class="project-tree-row project-file-row"
                :class="{ selected: row.path === activeProjectFilePath, file: row.type === 'file', missing: row.exists === false }"
                :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
              >
                <button
                  v-if="fsRowIsDirectory(row)"
                  class="project-tree-toggle"
                  type="button"
                  :aria-label="projectExpandedPaths[row.path] ? '折叠目录' : '展开目录'"
                  :disabled="row.exists === false || projectFileLoadingPath === row.path"
                  @click="toggleProjectDirectory(row)"
                >
                  <span v-if="projectFileLoadingPath === row.path" class="mini-spinner" aria-hidden="true"></span>
                  <span v-else>{{ projectExpandedPaths[row.path] ? "⌄" : "›" }}</span>
                </button>
                <span v-else class="project-tree-spacer"></span>
                <button
                  class="project-tree-name"
                  type="button"
                  :title="row.path"
                  :disabled="row.exists === false || projectFileLoadingPath === row.path"
                  @click="row.type === 'file' ? openProjectFile(row) : toggleProjectDirectory(row)"
                >
                  <span
                    class="project-tree-icon"
                    :class="row.type === 'file' ? projectFileIconClass(row.name) : row.type"
                    aria-hidden="true"
                  >
                    {{ row.type === "file" ? "□" : "▰" }}
                  </span>
                  <span>{{ row.name }}</span>
                  <small v-if="row.type === 'file'">{{ formatBytes(row.size || 0) }}</small>
                  <small v-else-if="row.exists === false">不存在</small>
                </button>
              </div>
            </div>
            <p class="project-file-preview-hint">文件点击后仅选中；预览待上线。</p>
          </section>
          <section class="result-sidebar-section">
            <div class="result-panel-header">
              <div><span>结果</span><strong>{{ activeOutputs.length }}</strong></div>
              <small>仅显示已确认保存的内容</small>
            </div>
            <div class="output-list">
              <div v-if="activeOutputs.length === 0" class="output-empty-state">
                <span class="output-empty-icon">○</span>
                <strong>当前对话还没有已保存结果</strong>
                <p>确认保存待办、文档、知识条目或文件后，会显示在这里。</p>
              </div>
              <button
                v-for="output in activeOutputs"
                :key="output.id"
                class="output-card sticky-note"
                :class="[`note-${output.kind}`, { active: activeArtifact?.id === output.id }]"
                type="button"
                @click="openOutput(output)"
              >
                <div class="output-card-top">
                  <span class="output-kind" :class="output.kind">{{ outputKindLabel(output.kind, output.label) }}</span>
                  <span class="output-status" :class="output.status">{{ outputStatusLabel(output.status, output.saved) }}</span>
                </div>
                <strong>{{ output.title }}</strong>
                <p>{{ output.summary }}</p>
                <small>{{ output.sourceCount ? `${output.sourceCount} 项资料 · ` : "" }}{{ output.updated }}</small>
              </button>
            </div>
          </section>
          <section v-if="activePlan && activeConversationId && !planGeneratingByConversation[activeConversationId]" class="plan-sidebar-section">
            <div class="result-panel-header">
              <div><span>当前计划</span><strong>{{ activePlan.completedSteps }}/{{ activePlan.steps.length }}</strong></div>
              <small>{{ activePlan.status === "completed" ? "已完成" : activePlan.status === "executing" ? "执行中" : "待执行" }}</small>
            </div>
            <div class="plan-result-card">
              <div class="plan-result-header">
                <span>执行计划</span>
                <small>{{ activePlan.status === "executing" ? "正在推进" : activePlan.status === "completed" ? "已完成" : "未执行" }}</small>
              </div>
              <strong>{{ activePlan.title }}</strong>
              <button
                v-if="activeConversationId && (activePlan.objective || activePlan.constraints.length || activePlan.successCriteria.length)"
                type="button"
                class="plan-overview-toggle"
                :aria-expanded="isPlanOverviewExpanded(activeConversationId)"
                @click="togglePlanOverview(activeConversationId)"
              >
                {{ isPlanOverviewExpanded(activeConversationId) ? "收起计划说明" : "查看计划说明" }}
              </button>
              <div v-if="activeConversationId && isPlanOverviewExpanded(activeConversationId)" class="plan-overview">
                <p v-if="activePlan.objective">{{ activePlan.objective }}</p>
                <div v-if="activePlan.constraints.length" class="plan-tags">
                  <span v-for="constraint in activePlan.constraints" :key="constraint">{{ constraint }}</span>
                </div>
                <div v-if="activePlan.successCriteria.length" class="plan-success-criteria">
                  <span>完成标准</span>
                  <ul>
                    <li v-for="criterion in activePlan.successCriteria" :key="criterion">{{ criterion }}</li>
                  </ul>
                </div>
              </div>
              <ol class="plan-step-list" :class="{ complete: activePlan.status === 'completed' }">
                <li
                  v-for="(step, index) in activePlan.steps"
                  :key="step.id"
                  :class="[`plan-step-${step.status}`, { expanded: activeConversationId && isPlanStepExpanded(activeConversationId, step) }]"
                >
                  <span class="plan-step-node" aria-hidden="true">{{ step.status === "done" ? "✓" : index + 1 }}</span>
                  <div class="plan-step-body">
                    <button
                      v-if="activeConversationId"
                      type="button"
                      class="plan-step-toggle"
                      :aria-expanded="isPlanStepExpanded(activeConversationId, step)"
                      :aria-controls="`plan-step-detail-${activeConversationId}-${step.id}`"
                      @click="togglePlanStep(activeConversationId, step)"
                    >
                      <strong>{{ step.title }}</strong>
                      <span class="plan-step-chevron" aria-hidden="true"></span>
                    </button>
                    <div
                      v-if="activeConversationId && isPlanStepExpanded(activeConversationId, step)"
                      :id="`plan-step-detail-${activeConversationId}-${step.id}`"
                      class="plan-step-detail"
                    >
                      <p v-if="step.detail">{{ step.detail }}</p>
                      <small v-if="step.deliverable">产出：{{ step.deliverable }}</small>
                    </div>
                  </div>
                </li>
              </ol>
              <div class="plan-result-actions">
                <button type="button" @click="continuePlan">继续完善</button>
                <button type="button" class="primary" @click="executePlan">交给 Agent</button>
              </div>
            </div>
          </section>
          <div class="result-panel-footer"><span></span>结果只保留已确认保存的内容</div>
        </aside>
      </section>

      <section v-else-if="activeView === 'knowledge'" class="knowledge-layout">
        <div class="panel-header">
          <div>
            <h2>Documents</h2>
            <p>Upload, index, retry, and audit personal knowledge sources.</p>
          </div>
          <div class="panel-actions">
            <input
              v-model="knowledgeSearch"
              class="search-field"
              aria-label="Search documents"
              placeholder="Search documents"
            />
            <select v-model="knowledgeFilter" aria-label="Filter documents">
              <option value="all">All</option>
              <option value="indexed">Indexed</option>
              <option value="queued">Queued</option>
              <option value="parsing">Parsing</option>
              <option value="failed">Failed</option>
            </select>
            <label class="upload-button compact">
              <input type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx" @change="uploadDocuments" />
              <span class="button-icon">↑</span>
              <span>Upload</span>
            </label>
          </div>
        </div>
        <div class="metric-grid">
          <article>
            <strong>{{ documents.length }}</strong>
            <span>Total documents</span>
          </article>
          <article>
            <strong>{{ indexedDocumentCount }}</strong>
            <span>Indexed</span>
          </article>
          <article>
            <strong>{{ failedDocumentCount }}</strong>
            <span>Needs review</span>
          </article>
        </div>
        <div v-if="backendError && activeView === 'knowledge'" class="backend-error">
          {{ backendError }}
        </div>
        <div class="document-table">
          <div class="table-row header">
            <span>Name</span>
            <span>Status</span>
            <span>Collection</span>
            <span>Chunks</span>
            <span>Updated</span>
            <span></span>
          </div>
          <div v-if="filteredDocuments.length === 0" class="table-row empty-row">No documents found</div>
          <div v-for="document in filteredDocuments" :key="document.id" class="table-row">
            <span>
              <strong>{{ document.name }}</strong>
              <small>{{ documentType(document) }}</small>
            </span>
            <span class="status-pill" :class="document.status">{{ document.status }}</span>
            <span>{{ collectionName(document.collection_id) }}</span>
            <span>{{ document.chunk_count }}</span>
            <span>{{ displayDate(document.updated_at) }}</span>
            <span>
              <button
                class="row-action"
                type="button"
                :aria-label="`Delete ${document.name}`"
                title="Delete document"
                @click="removeDocument(document.id)"
              >
                Delete
              </button>
            </span>
          </div>
        </div>
      </section>
      <section v-else class="settings-layout">
        <nav class="settings-tabs" aria-label="Settings sections">
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'profile' }"
            @click="activeSettingsPanel = 'profile'"
          >
            Profile
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'personalization' }"
            @click="activeSettingsPanel = 'personalization'"
          >
            Personal
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'runtime' }"
            @click="activeSettingsPanel = 'runtime'"
          >
            Runtime
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'reminders' }"
            @click="activeSettingsPanel = 'reminders'"
          >
            Reminders
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'models' }"
            @click="activeSettingsPanel = 'models'"
          >
            Models
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'skills' }"
            @click="activeSettingsPanel = 'skills'"
          >
            Skills
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'keys' }"
            @click="activeSettingsPanel = 'keys'"
          >
            API Keys
          </button>
          <button
            type="button"
            :class="{ active: activeSettingsPanel === 'privacy' }"
            @click="activeSettingsPanel = 'privacy'"
          >
            Privacy
          </button>
        </nav>

        <div v-if="activeSettingsPanel === 'profile'" class="settings-panel two-column">
          <div class="panel-card floating-window-settings-card">
            <label class="setting-toggle-row">
              <span>
                <strong>悬浮窗</strong>
                <small>Floating Window · 最小化主窗口后显示桌宠浮窗</small>
              </span>
              <input
                v-model="petFloatEnabled"
                type="checkbox"
                role="switch"
                @change="savePetFloatEnabled"
              />
              <span class="toggle" :class="{ enabled: petFloatEnabled }" aria-hidden="true">
                <span></span>
              </span>
            </label>
            <p v-if="petFloatSettingError" class="test-result">{{ petFloatSettingError }}</p>
          </div>
          <div class="panel-card profile-card">
            <span class="avatar large">{{ userInitials }}</span>
            <div>
              <label>
                Display name
                <input v-model="profile.display_name" />
              </label>
              <label>
                App data path
                <input :value="profile.data_dir || '~/Library/Application Support/DeskBits'" readonly />
              </label>
              <div class="settings-facts">
                <span>Runtime</span>
                <strong>{{ healthInfo?.status ?? "offline" }}</strong>
                <span>Version</span>
                <strong>{{ runtimeInfo?.version ?? healthInfo?.version ?? "unknown" }}</strong>
              </div>
              <button class="save-button" type="button" @click="saveProfile">
                {{ profileSaved ? "Saved ✓" : "Save Profile" }}
              </button>
            </div>
          </div>
          <div class="panel-card">
            <h3>Local Workspace</h3>
            <div class="settings-facts">
              <span>Authentication</span>
              <strong>{{ runtimeInfo?.auth_enabled ? "Token enabled" : "Local only" }}</strong>
              <span>Features</span>
              <strong>{{ runtimeFeatureList.length }}</strong>
              <span>Sessions</span>
              <strong>{{ conversations.length }}</strong>
              <span>Documents</span>
              <strong>{{ documents.length }}</strong>
            </div>
            <div v-if="runtimeFeatureList.length" class="feature-list">
              <span v-for="feature in runtimeFeatureList" :key="feature">{{ feature }}</span>
            </div>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'personalization'" class="settings-panel">
          <div class="panel-card personalization-header">
            <div>
              <h3>Desktop Pet</h3>
              <p>Choose the companion that stays in the lower-right corner and later becomes the floating pet.</p>
            </div>
            <div class="selected-pet-preview">
              <span
                class="settings-pet-sprite"
                :style="{
                  backgroundImage: `url(${petSpriteUrl})`,
                  backgroundPosition: selectedPetOption.position
                }"
                aria-hidden="true"
              ></span>
              <strong>{{ selectedPetOption.label }}</strong>
            </div>
          </div>
          <div class="pet-choice-grid">
            <button
              v-for="pet in petOptions"
              :key="pet.id"
              type="button"
              class="pet-choice-card"
              :class="{ active: selectedPet === pet.id }"
              @click="selectPet(pet.id)"
            >
              <span
                class="settings-pet-sprite"
                :style="{
                  backgroundImage: `url(${petSpriteUrl})`,
                  backgroundPosition: pet.position
                }"
                aria-hidden="true"
              ></span>
              <strong>{{ pet.label }}</strong>
              <small>{{ selectedPet === pet.id ? "Selected" : "Choose" }}</small>
            </button>
          </div>
          <div class="notice-card">
            <strong>Floating Window</strong>
            <label class="setting-toggle-row">
              <span>
                <strong>最小化后显示浮窗</strong>
                <small>Show the pet when the main DeskBits window is minimized.</small>
              </span>
              <input
                v-model="petFloatEnabled"
                type="checkbox"
                role="switch"
                @change="savePetFloatEnabled"
              />
              <span class="toggle" :class="{ enabled: petFloatEnabled }" aria-hidden="true">
                <span></span>
              </span>
            </label>
            <p v-if="petFloatSettingError" class="test-result">{{ petFloatSettingError }}</p>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'runtime'" class="settings-panel two-column">
          <div class="panel-card">
            <h3>Runtime Status</h3>
            <div class="settings-facts">
              <span>Service</span>
              <strong>{{ healthInfo?.status ?? "offline" }}</strong>
              <span>Model</span>
              <strong>{{ runtimeModelLabel }}</strong>
              <span>Vision</span>
              <strong>{{ runtimeInfo?.model?.vision_enabled ? "Available" : "Unknown" }}</strong>
              <span>Runs</span>
              <strong>{{ runtimeStatus?.active_runs ?? 0 }}</strong>
              <span>Permissions</span>
              <strong>{{ runtimeStatus?.pending_permissions ?? 0 }}</strong>
              <span>Due reminders</span>
              <strong>{{ runtimeStatus?.due_reminders ?? reminders.filter((item) => item.status === "active").length }}</strong>
            </div>
            <p v-if="runtimeLastMessage" class="muted-line">{{ runtimeLastMessage }}</p>
            <p v-if="runtimePanelError" class="test-result">{{ runtimePanelError }}</p>
            <button class="save-button" type="button" @click="loadRuntimePanel">Refresh Runtime</button>
          </div>
          <div class="panel-card">
            <h3>Speech</h3>
            <div class="settings-facts">
              <span>Status</span>
              <strong>{{ speechStatusLabel }}</strong>
              <span>Device</span>
              <strong>{{ speechStatus?.device ?? runtimeInfo?.speech?.device ?? "unknown" }}</strong>
              <span>Language</span>
              <strong>{{ speechStatus?.language ?? "auto" }}</strong>
            </div>
            <p v-if="speechStatus?.model_dir" class="muted-line">{{ speechStatus.model_dir }}</p>
          </div>
          <div class="panel-card">
            <h3>Recent Events</h3>
            <div v-if="runtimeEvents.length" class="runtime-event-list">
              <div v-for="event in runtimeEvents.slice(0, 5)" :key="event.id" class="runtime-event-row">
                <span>
                  <strong>{{ event.type.replace(/_/g, " ") }}</strong>
                  <small>{{ formatTime(event.created_at) }}</small>
                </span>
              </div>
            </div>
            <div v-else class="empty-panel compact">
              <h3>No runtime events</h3>
              <p>Events appear here after the runtime event API is available.</p>
            </div>
          </div>
          <div class="panel-card">
            <h3>Storage</h3>
            <div class="settings-facts">
              <span>Total</span>
              <strong>{{ formatBytes(storageTotalBytes) }}</strong>
              <span>Uploads</span>
              <strong>{{ formatBytes(storageUsage?.uploads_bytes ?? 0) }}</strong>
              <span>Knowledge</span>
              <strong>{{ formatBytes(storageUsage?.knowledge_bytes ?? 0) }}</strong>
              <span>Traces</span>
              <strong>{{ formatBytes(storageUsage?.traces_bytes ?? 0) }}</strong>
            </div>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'reminders'" class="settings-panel two-column">
          <div class="panel-card">
            <h3>New Reminder</h3>
            <label>
              Title
              <input v-model="newReminder.title" placeholder="Daily project check-in" />
            </label>
            <label>
              Notes
              <input v-model="newReminder.body" placeholder="What should DeskBits remind you about?" />
            </label>
            <label>
              Schedule
              <select v-model="newReminder.schedule_type">
                <option value="daily">daily</option>
                <option value="weekly">weekly</option>
                <option value="once">once</option>
              </select>
            </label>
            <label v-if="newReminder.schedule_type === 'once'">
              Due date
              <input v-model="newReminder.due_at" type="datetime-local" />
            </label>
            <label v-else>
              Time
              <input v-model="newReminder.time_of_day" type="time" />
            </label>
            <button class="save-button" type="button" @click="saveReminder">
              {{ reminderSaved ? "Saved ✓" : "Save Reminder" }}
            </button>
            <p v-if="remindersPanelError" class="test-result">{{ remindersPanelError }}</p>
          </div>
          <div class="reminder-list">
            <div v-for="reminder in reminders" :key="reminder.id" class="reminder-row">
              <span>
                <strong>{{ reminder.title }}</strong>
                <small>{{ reminderScheduleLabel(reminder) }} · {{ reminder.status }}</small>
                <small v-if="reminder.body">{{ reminder.body }}</small>
              </span>
              <div class="button-row compact-actions">
                <button type="button" @click="dismissReminder(reminder.id)">Dismiss</button>
                <button type="button" @click="completeReminder(reminder.id)">Done</button>
                <button type="button" @click="deleteReminder(reminder.id)">Delete</button>
              </div>
            </div>
            <div v-if="reminders.length === 0" class="empty-panel compact">
              <h3>No reminders yet</h3>
              <p>The list will sync with the reminders API when the backend slice lands.</p>
            </div>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'models'" class="settings-panel two-column">
          <div class="panel-card">
            <h3>Model Setup</h3>
            <label>
              Provider
              <select v-model="models.provider" @change="changeModelProvider">
                <option>openai</option>
                <option>deepseek</option>
                <option>tavily</option>
              </select>
            </label>
            <label>
              Available model
              <input
                v-model="modelCandidate"
                list="available-models"
                placeholder="Choose or enter a model ID"
              />
            </label>
            <datalist id="available-models">
              <option v-for="model in availableModels" :key="model" :value="model" />
            </datalist>
            <div class="button-row compact-actions">
              <button class="row-action" type="button" :disabled="availableModelsLoading" @click="loadAvailableModels()">
                {{ availableModelsLoading ? "Refreshing..." : "Refresh available models" }}
              </button>
              <button class="save-button" type="button" :disabled="!modelCandidate || modelTestLoading" @click="testAndAddModel">
                {{ modelTestLoading ? "Testing..." : "Test and add model" }}
              </button>
            </div>
            <p v-if="availableModelsError" class="muted-line">{{ availableModelsError }}</p>
            <p v-if="modelTestResult" class="test-result">{{ modelTestResult }}</p>
            <label>
              Active model
              <select v-model="selectedModel" :disabled="configuredModels.length === 0" @change="applySelectedModel">
                <option value="" disabled>Select a verified model</option>
                <option v-for="model in configuredModels" :key="configuredModelKey(model)" :value="configuredModelKey(model)">
                  {{ model.provider }} / {{ model.model }}
                </option>
              </select>
            </label>
            <button class="save-button" type="button" :disabled="!selectedModel" @click="saveModelsSettings">
              {{ modelsSaved ? "Saved ✓" : "Save generation settings" }}
            </button>
            <div v-if="configuredModels.length" class="api-key-list">
              <div v-for="model in configuredModels" :key="configuredModelKey(model)" class="api-key-row">
                <span>
                  <strong>{{ model.provider }} / {{ model.model }}</strong>
                  <small>Verified {{ displayDate(model.verified_at) }}</small>
                </span>
                <button
                  class="row-action"
                  type="button"
                  :disabled="model.provider === models.provider && model.model === models.model"
                  @click="removeConfiguredModel(model)"
                >
                  Remove
                </button>
              </div>
            </div>
          </div>
          <div class="panel-card">
            <h3>Generation</h3>
            <label>
              Temperature
              <input v-model.number="models.temperature" type="range" min="0" max="1" step="0.1" />
            </label>
            <div class="range-value">{{ models.temperature.toFixed(1) }}</div>
            <label>
              Context length
              <input v-model.number="models.context_length" type="number" min="1024" step="1024" />
            </label>
            <p>Saved model settings are read by the local runtime settings store.</p>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'skills'" class="settings-panel two-column">
          <div class="panel-card skill-import-panel">
            <h3>Skill Library</h3>
            <div class="settings-facts">
              <span>Installed</span>
              <strong>{{ skills.length }}</strong>
              <span>Enabled</span>
              <strong>{{ enabledSkillCount }}</strong>
              <span>Permissioned</span>
              <strong>{{ skills.filter((skill) => skill.requires_permission).length }}</strong>
            </div>
            <div
              class="skill-import-dropzone"
              :class="{ active: skillDropActive, importing: skillImporting }"
              @dragover="onSkillImportDragOver"
              @dragleave="onSkillImportDragLeave"
              @drop="onSkillImportDrop"
            >
              <input ref="skillImportInput" type="file" accept=".zip" @change="importSkillPackage" />
              <span class="skill-import-mark" aria-hidden="true">↓</span>
              <strong>{{ skillImporting ? "正在导入 Skill 包" : "拖拽 .zip 文件到这里" }}</strong>
              <small>{{ skillImporting ? "正在校验并安装，请稍候" : "或选择本地 Skill 包导入" }}</small>
              <button type="button" :disabled="skillImporting" @click="openSkillFilePicker">选择 .zip 文件</button>
            </div>
            <div v-if="skillImporting || skillImportProgress > 0" class="skill-import-progress" aria-live="polite">
              <span><i :style="{ width: `${skillImportProgress}%` }"></i></span>
              <small>{{ skillImportProgress }}%</small>
            </div>
            <p v-if="skillImportStatus" class="skill-import-message success">{{ skillImportStatus }}</p>
            <p v-if="skillImportError" class="skill-import-message error">{{ skillImportError }}</p>
          </div>
          <div class="skill-library-list">
            <div v-if="skills.length === 0" class="empty-panel compact">
              <span class="skill-empty-mark" aria-hidden="true">✦</span>
              <h3>导入 Skill 包让 Agent 获得新能力</h3>
              <p>每个 Skill 会在权限确认后独立启用，不会自动执行。</p>
              <button type="button" @click="showSkillPackageExample">查看示例包说明</button>
            </div>
            <template v-else>
              <section v-if="builtinSkills.length > 0" class="skill-group">
                <header><span>内置工具</span><small>{{ builtinSkills.length }}</small></header>
                <article v-for="skill in builtinSkills" :key="skill.name" class="skill-card">
                  <span class="skill-type-icon" :class="skill.type === 'prompt' ? 'prompt' : 'tool'" aria-hidden="true">{{ skill.type === "prompt" ? "📝" : "🔧" }}</span>
                  <div class="skill-card-copy">
                    <div class="skill-card-title"><strong>{{ skill.name }}</strong><span>v{{ skill.version || "1.0.0" }}</span></div>
                    <p>{{ skill.description }}</p>
                    <div v-if="skill.type === 'prompt'" class="skill-prompt-preview">
                      <p>{{ expandedSkillNames[skill.name] ? (skill.prompt || "暂无提示词内容") : `${(skill.prompt || "暂无提示词内容").slice(0, 200)}${(skill.prompt || "").length > 200 ? "..." : ""}` }}</p>
                      <button type="button" @click="toggleSkillPromptPreview(skill.name)">{{ expandedSkillNames[skill.name] ? "收起" : "预览提示词" }}</button>
                    </div>
                  </div>
                  <span class="skill-risk" :class="`risk-${skill.risk_level.toLowerCase()}`">{{ skill.risk_level }}</span>
                  <button class="toggle skill-toggle" type="button" :class="{ enabled: skill.enabled }" :aria-label="skill.enabled ? `关闭 ${skill.name}` : `启用 ${skill.name}`" @click="toggleSkill(skill.name)"><span></span></button>
                </article>
              </section>
              <section v-if="importedSkills.length > 0" class="skill-group">
                <header><span>用户导入</span><small>{{ importedSkills.length }}</small></header>
                <article v-for="skill in importedSkills" :key="skill.name" class="skill-card">
                  <span class="skill-type-icon" :class="skill.type === 'prompt' ? 'prompt' : 'tool'" aria-hidden="true">{{ skill.type === "prompt" ? "📝" : "🔧" }}</span>
                  <div class="skill-card-copy">
                    <div class="skill-card-title"><strong>{{ skill.name }}</strong><span>v{{ skill.version || "1.0.0" }}</span></div>
                    <p>{{ skill.description }}</p>
                    <div v-if="skill.type === 'prompt'" class="skill-prompt-preview">
                      <p>{{ expandedSkillNames[skill.name] ? (skill.prompt || "暂无提示词内容") : `${(skill.prompt || "暂无提示词内容").slice(0, 200)}${(skill.prompt || "").length > 200 ? "..." : ""}` }}</p>
                      <button type="button" @click="toggleSkillPromptPreview(skill.name)">{{ expandedSkillNames[skill.name] ? "收起" : "预览提示词" }}</button>
                    </div>
                  </div>
                  <span class="skill-risk" :class="`risk-${skill.risk_level.toLowerCase()}`">{{ skill.risk_level }}</span>
                  <button class="toggle skill-toggle" type="button" :class="{ enabled: skill.enabled }" :aria-label="skill.enabled ? `关闭 ${skill.name}` : `启用 ${skill.name}`" @click="toggleSkill(skill.name)"><span></span></button>
                  <button class="skill-delete" type="button" :aria-label="`删除 ${skill.name}`" title="删除 Skill" @click="removeSkill(skill.name)">×</button>
                </article>
              </section>
            </template>
          </div>
        </div>

        <div v-else-if="activeSettingsPanel === 'keys'" class="settings-panel two-column">
          <div class="panel-card">
            <h3>API Keys</h3>
            <div v-for="key in apiKeys" :key="key.provider" class="api-key-row">
              <span>
                <strong>{{ key.provider }}</strong>
                <small>{{ key.masked ?? "not set" }}</small>
                <small v-if="key.verified_at">Verified {{ displayDate(key.verified_at) }}</small>
              </span>
              <button class="row-action" type="button" @click="removeApiKey(key.provider)">
                Remove
              </button>
            </div>
            <div v-if="apiKeys.length === 0" class="muted-line">No keys stored.</div>
            <label>
              Provider
              <select v-model="apiKeyProvider">
                <option>openai</option>
                <option>deepseek</option>
              </select>
            </label>
            <label>
              API Key
              <input
                v-model="apiKeyInput"
                type="password"
                autocomplete="off"
                placeholder="Stored by the local service"
              />
            </label>
            <div class="button-row">
              <button class="save-button" type="button" @click="saveNewApiKey">
                {{ apiKeySaved ? "Saved ✓" : "Save Key" }}
              </button>
              <button type="button" @click="testApiKeyConnection">Test Connection</button>
            </div>
            <p v-if="apiKeyTestResult" class="test-result">{{ apiKeyTestResult }}</p>
          </div>
          <div class="notice-card">
            <strong>Runtime source</strong>
            <p>{{ runtimeKeySource }}</p>
            <h3>Encrypted Store</h3>
            <p>Keys are encrypted on disk by the local service and never returned in plaintext.</p>
            <h3>Data Location</h3>
            <p>{{ profile.data_dir || "~/Library/Application Support/DeskBits" }}</p>
          </div>
        </div>

        <div v-else class="settings-panel two-column">
          <div class="panel-card">
            <h3>Local Data</h3>
            <p>{{ profile.data_dir || "~/Library/Application Support/DeskBits" }}</p>
            <div class="privacy-grid">
              <span>Sessions</span>
              <strong>{{ conversations.length }}</strong>
              <span>Documents</span>
              <strong>{{ documents.length }}</strong>
            </div>
          </div>
          <div class="notice-card">
            <strong>Review before action</strong>
            <p>Skill permissions and encrypted keys remain explicit in the desktop shell.</p>
          </div>
        </div>
      </section>
    </main>
    <div v-if="projectDialogOpen" class="workspace-modal-backdrop" @click.self="projectDialogOpen = false">
      <section class="workspace-modal project-add-modal" role="dialog" aria-modal="true" aria-labelledby="add-project-title">
        <p class="panel-kicker">项目管理</p>
        <h2 id="add-project-title">添加项目文件夹</h2>
        <p>从 Finder 选择项目文件夹。只会保存项目引用，不会移动或删除磁盘文件。</p>
        <button class="project-directory-picker" type="button" @click="chooseProjectDirectory">
          <span>📁</span>
          <strong>{{ projectDraftPath ? "重新选择文件夹" : "从 Finder 选择文件夹" }}</strong>
          <small>{{ projectDraftPath || "推荐使用这个入口添加项目" }}</small>
        </button>
        <label class="project-form-field">
          <span>项目路径</span>
          <input v-model="projectDraftPath" placeholder="/Users/me/work/my-app" />
        </label>
        <label class="project-form-field">
          <span>项目名称（可选）</span>
          <input v-model="projectDraftName" placeholder="默认使用目录名" />
        </label>
        <p v-if="projectError" class="project-context-error">{{ projectError }}</p>
        <div class="workspace-modal-actions">
          <button type="button" @click="projectDialogOpen = false">取消</button>
          <button class="primary" type="button" :disabled="projectAdding" @click="submitAddProject">
            {{ projectAdding ? "添加中" : "添加项目" }}
          </button>
        </div>
      </section>
    </div>
    <div v-if="saveConfirmationOpen" class="workspace-modal-backdrop" @click.self="saveConfirmationOpen = false">
      <section class="workspace-modal" role="dialog" aria-modal="true" aria-labelledby="save-workspace-title">
        <p class="panel-kicker">{{ activeArtifact?.taskDraft ? "确认待办" : "保存成果" }}</p>
        <h2 id="save-workspace-title">{{ activeArtifact?.taskDraft ? "加入待办清单？" : "保存到工作区？" }}</h2>
        <p v-if="activeArtifact?.taskDraft">确认后会创建这项待办{{ activeArtifact.taskDraft.reminder ? "并设置一次性提醒" : "" }}，同时写入本次对话记忆。未确认前不会出现在工作区。</p>
        <p v-else>“{{ activeArtifact?.title }}”会保存为可再次打开的工作区内容，之后可以添加到其他对话继续处理。</p>
        <div class="workspace-modal-actions">
          <button type="button" @click="saveConfirmationOpen = false">取消</button>
          <button class="primary" type="button" @click="confirmSaveArtifact">{{ activeArtifact?.taskDraft ? "确认加入" : "确认保存" }}</button>
        </div>
      </section>
    </div>
    <div v-if="conversationPickerOpen" class="workspace-modal-backdrop" @click.self="conversationPickerOpen = false">
      <section class="workspace-modal conversation-picker" role="dialog" aria-modal="true" aria-labelledby="add-to-conversation-title">
        <p class="panel-kicker">添加工作区内容</p>
        <h2 id="add-to-conversation-title">选择继续工作的对话</h2>
        <p>“{{ pendingWorkspaceItem?.title }}”会作为上下文带入目标对话。</p>
        <div class="conversation-picker-list">
          <button v-for="conversation in conversations" :key="conversation.id" type="button" @click="addWorkspaceItemToConversation(conversation.id)">
            <span>{{ conversation.title }}</span><small>{{ conversation.date }}</small>
          </button>
          <button class="new-picker-option" type="button" @click="addWorkspaceItemToConversation('new')">
            <span>＋ 新建对话</span><small>从工作区内容开始</small>
          </button>
        </div>
      </section>
    </div>
    <aside
      v-if="activeView !== 'artifact'"
      ref="mainPetDock"
      class="pet-dock"
      :class="{ dragging: mainPetDragging }"
      :style="mainPetDockStyle"
      aria-label="DeskBits pet"
    >
      <button
        v-if="petBubble"
        class="pet-bubble"
        type="button"
        :class="petBubble.kind"
        :aria-label="petBubble.label"
        :title="petBubble.label"
        @pointerdown="handleMainPetPointerDown"
        @pointermove="handleMainPetPointerMove"
        @pointerup="handleMainPetPointerUp"
        @pointercancel="handleMainPetPointerCancel"
        @click="handleMainPetBubbleClick"
      >
        <span
          class="pet-bubble-sprite"
          :style="{
            backgroundImage: `url(${petBubbleSpriteUrl})`,
            backgroundPosition: petBubble.position
          }"
          aria-hidden="true"
        ></span>
      </button>
      <!-- 文件预览弹窗（工作台/文件视图共用） -->
      <div v-if="previewModal || previewModalError" class="preview-overlay" @click.self="closeFilePreview">
        <section class="preview-modal panel-card">
          <div class="scenario-panel-header">
            <h2>{{ previewModal?.name ?? "预览失败" }}</h2>
            <div class="file-preview-meta">
              <span v-if="previewModal" class="output-kind" :class="previewModal.kind">{{ previewKindLabel(previewModal.kind) }}</span>
              <span v-if="previewModal">{{ formatBytes(previewModal.size) }}</span>
              <button class="preview-close" type="button" aria-label="关闭预览" @click="closeFilePreview">×</button>
            </div>
          </div>
          <p v-if="previewModalError" class="preview-error">{{ previewModalError }}</p>
          <template v-else-if="previewModal">
            <p v-if="previewModal.truncated" class="preview-truncated">内容过长，已截断前 10 万字符</p>
            <pre class="preview-content">{{ previewModal.content }}</pre>
            <div class="preview-actions">
              <button class="save-button" type="button" @click="saveFileToWorkspace(previewModal!)">保存到工作台</button>
            </div>
          </template>
        </section>
      </div>

      <button
        class="pet-main-button"
        type="button"
        :aria-label="`单击与 ${selectedPetOption.label} 互动，长按拖动位置`"
        :title="`单击与 ${selectedPetOption.label} 互动，长按拖动位置`"
        @pointerdown="handleMainPetPointerDown"
        @pointermove="handleMainPetPointerMove"
        @pointerup="handleMainPetPointerUp"
        @pointercancel="handleMainPetPointerCancel"
        @click="handleMainPetClick"
        @contextmenu.prevent
      >
        <span
          class="pet-sprite"
          :class="petMotionClass"
          :style="{
            backgroundImage: `url(${petSpriteUrl})`,
            backgroundPosition: selectedPetOption.position
          }"
          aria-hidden="true"
        ></span>
        <span class="pet-wave one" aria-hidden="true"></span>
        <span class="pet-wave two" aria-hidden="true"></span>
      </button>
    </aside>
    <div
      v-if="attachmentPreview"
      class="preview-overlay attachment-preview-overlay"
      @click.self="closeAttachmentPreview"
    >
      <section class="preview-modal attachment-preview-modal panel-card" :class="`mode-${attachmentPreview.mode}`">
        <div class="scenario-panel-header attachment-preview-header">
          <div class="attachment-preview-title">
            <span class="attachment-preview-badge" :class="`mode-${attachmentPreview.mode}`">
              {{ attachmentPreviewLabel(attachmentPreview.mode) }}
            </span>
            <h2 :title="attachmentPreview.attachment.filename">{{ attachmentDisplayName(attachmentPreview.attachment) }}</h2>
          </div>
          <div class="file-preview-meta attachment-preview-meta">
            <span>{{ formatFileSize(attachmentPreview.attachment.size) }}</span>
            <button class="preview-close" type="button" aria-label="关闭附件预览" @click="closeAttachmentPreview">×</button>
          </div>
        </div>

        <p v-if="attachmentPreview.loading" class="attachment-preview-loading">
          <span class="attachment-preview-spinner" aria-hidden="true"></span>
          正在加载预览…
        </p>
        <p v-else-if="attachmentPreview.error" class="preview-error">{{ attachmentPreview.error }}</p>
        <template v-else>
          <div v-if="attachmentPreview.mode === 'image'" class="attachment-preview-figure">
            <img :src="attachmentPreview.url" :alt="attachmentPreview.attachment.filename" />
          </div>
          <pre v-else-if="attachmentPreview.mode === 'text'" class="preview-content attachment-preview-content">{{ attachmentPreview.content }}</pre>
          <div v-else-if="attachmentPreview.mode === 'pdf'" class="attachment-preview-frame">
            <iframe :src="attachmentPreview.url" :title="attachmentPreview.attachment.filename" />
          </div>
          <div v-else class="attachment-preview-filecard">
            <span class="attachment-preview-fileicon" :class="`mode-${attachmentPreview.mode}`" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M6.25 3.75h7.35L18.75 8.9v10.85a.5.5 0 0 1-.5.5h-12a.5.5 0 0 1-.5-.5V4.25a.5.5 0 0 1 .5-.5Z" />
                <path d="M13.5 4v4.75h4.75" />
              </svg>
            </span>
            <strong>当前暂不支持直接内嵌预览 Word 文档</strong>
            <p>你可以打开原文件交给系统应用查看，也可以在 Finder 中定位这个附件。</p>
          </div>

          <p v-if="attachmentPreview.mode === 'text' && attachmentPreview.truncated" class="preview-truncated">内容过长，仅预览前一部分</p>

          <div class="preview-actions attachment-preview-actions">
            <button class="attachment-preview-button primary" type="button" @click="openAttachmentInSystem">打开原文件</button>
            <button class="attachment-preview-button secondary" type="button" @click="revealAttachmentInFinder">在 Finder 中显示</button>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
