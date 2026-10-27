const { app, BrowserWindow, dialog, ipcMain, screen, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { AgentServiceManager } = require("./agent-service.cjs");

function getArgValue(name) {
  const prefix = `${name}=`;
  const item = process.argv.find((argument) => argument.startsWith(prefix));
  return item ? item.slice(prefix.length) : undefined;
}

function getRendererUrl() {
  return getArgValue("--renderer-url") || process.env.DESKTOPMATE_RENDERER_URL;
}

function getSettingsPath() {
  return path.join(app.getPath("userData"), settingsFileName);
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(getSettingsPath(), "utf8"));
  } catch {
    return {};
  }
}

function writeSettings(settings) {
  fs.mkdirSync(app.getPath("userData"), { recursive: true });
  fs.writeFileSync(getSettingsPath(), `${JSON.stringify(settings, null, 2)}\n`, "utf8");
}

function getPetFloatEnabled() {
  const settings = readSettings();
  return settings.petFloatEnabled !== false;
}

function rendererUrlWithMode(mode) {
  const rendererUrl = getRendererUrl();
  if (!rendererUrl) {
    return null;
  }
  const url = new URL(rendererUrl);
  if (mode) {
    url.searchParams.set("mode", mode);
  }
  return url.toString();
}

function loadRenderer(window, mode) {
  const rendererUrl = rendererUrlWithMode(mode);
  if (rendererUrl) {
    window.loadURL(rendererUrl);
    return;
  }
  window.loadFile(path.join(__dirname, "../dist/index.html"), {
    query: mode ? { mode } : undefined
  });
}

// 子进程管理器：未配置 DESKTOPMATE_AGENT_SERVICE_URL 时自动拉起本地 Python 服务。
const agentService = new AgentServiceManager();
const appIconPath = path.join(__dirname, "../src/assets/deskbits-icon.png");
const settingsFileName = "deskbits-settings.json";
let mainWindow = null;
let petWindow = null;
let petSpeechProcess = null;
let isQuitting = false;

const PET_WINDOW_SIZES = {
  compact: { width: 208, height: 236 },
  conversation: { width: 352, height: 384 }
};

app.setName("DeskBits");

// 兼容显式传入的命令行参数（外部服务模式）。
if (getArgValue("--agent-service-url")) {
  process.env.DESKTOPMATE_AGENT_SERVICE_URL = getArgValue("--agent-service-url");
}
if (getArgValue("--agent-auth-token")) {
  process.env.DESKTOPMATE_AGENT_AUTH_TOKEN = getArgValue("--agent-auth-token");
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    return mainWindow;
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 760,
    title: "DeskBits",
    icon: appIconPath,
    backgroundColor: "#eef2ef",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.on("minimize", () => {
    if (getPetFloatEnabled()) {
      showPetWindow();
    }
  });

  mainWindow.on("restore", () => {
    hidePetWindow();
  });

  mainWindow.on("show", () => {
    if (!mainWindow.isMinimized()) {
      hidePetWindow();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
    if (process.platform !== "darwin") {
      app.quit();
      return;
    }
    hidePetWindow();
  });

  loadRenderer(mainWindow, "");
  return mainWindow;
}

function createPetWindow() {
  if (petWindow && !petWindow.isDestroyed()) {
    return petWindow;
  }

  petWindow = new BrowserWindow({
    width: PET_WINDOW_SIZES.compact.width,
    height: PET_WINDOW_SIZES.compact.height,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    transparent: true,
    show: false,
    title: "DeskBits Pet",
    icon: appIconPath,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  petWindow.on("closed", () => {
    petWindow = null;
  });

  loadRenderer(petWindow, "pet");
  return petWindow;
}

function positionPetWindow(window) {
  const margin = 16;
  const { workArea } = screen.getPrimaryDisplay();
  const bounds = window.getBounds();
  const x = Math.round(workArea.x + workArea.width - bounds.width - margin);
  const y = Math.round(workArea.y + workArea.height - bounds.height - margin);
  window.setPosition(x, y, false);
}

function clampPetWindowPosition(x, y, width, height) {
  const display = screen.getDisplayNearestPoint({
    x: Math.round(x + width / 2),
    y: Math.round(y + height / 2)
  });
  const { workArea } = display;
  const maxX = workArea.x + workArea.width - width;
  const maxY = workArea.y + workArea.height - height;
  return {
    x: Math.round(Math.min(Math.max(x, workArea.x), maxX)),
    y: Math.round(Math.min(Math.max(y, workArea.y), maxY))
  };
}

function getSavedPetWindowPosition(width, height) {
  const settings = readSettings();
  const position = settings.petWindowPosition;
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) {
    return null;
  }
  return clampPetWindowPosition(position.x, position.y, width, height);
}

function savePetWindowPosition(window) {
  if (!window || window.isDestroyed()) {
    return;
  }
  const bounds = window.getBounds();
  const settings = readSettings();
  settings.petWindowPosition = { x: bounds.x, y: bounds.y };
  writeSettings(settings);
}

function restoreSavedPetWindowPosition(window) {
  if (!window || window.isDestroyed()) {
    return false;
  }
  const bounds = window.getBounds();
  const position = getSavedPetWindowPosition(bounds.width, bounds.height);
  if (!position) {
    return false;
  }
  window.setPosition(position.x, position.y, false);
  return true;
}

function movePetWindowByDelta(deltaX, deltaY) {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }
  const bounds = petWindow.getBounds();
  const position = clampPetWindowPosition(
    bounds.x + deltaX,
    bounds.y + deltaY,
    bounds.width,
    bounds.height
  );
  petWindow.setPosition(position.x, position.y, false);
  savePetWindowPosition(petWindow);
}

function setPetWindowMode(mode) {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }
  const size = PET_WINDOW_SIZES[mode] || PET_WINDOW_SIZES.compact;
  const previousBounds = petWindow.getBounds();
  const nextX = previousBounds.x + previousBounds.width - size.width;
  const nextY = previousBounds.y + previousBounds.height - size.height;
  const position = clampPetWindowPosition(nextX, nextY, size.width, size.height);
  petWindow.setSize(size.width, size.height, true);
  petWindow.setPosition(position.x, position.y, false);
  savePetWindowPosition(petWindow);
}

function stopPetSpeech() {
  if (petSpeechProcess && !petSpeechProcess.killed) {
    petSpeechProcess.kill();
  }
  petSpeechProcess = null;
}

function speakPetReply(text) {
  stopPetSpeech();
  if (process.platform !== "darwin") {
    return false;
  }
  const safeText = String(text || "").replace(/\s+/g, " ").trim().slice(0, 320);
  if (!safeText) {
    return false;
  }
  petSpeechProcess = spawn("say", [safeText], { stdio: "ignore" });
  petSpeechProcess.once("exit", () => {
    petSpeechProcess = null;
  });
  return true;
}

function showPetWindow() {
  if (isQuitting || !mainWindow || !mainWindow.isMinimized() || !getPetFloatEnabled()) {
    return;
  }
  const window = createPetWindow();
  if (!restoreSavedPetWindowPosition(window)) {
    positionPetWindow(window);
  }
  if (!window.isVisible()) {
    window.showInactive();
  }
}

function hidePetWindow() {
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.hide();
  }
}

function restoreMainWindow() {
  hidePetWindow();
  if (!mainWindow || mainWindow.isDestroyed()) {
    mainWindow = createWindow();
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function setPetFloatEnabled(enabled) {
  const settings = readSettings();
  settings.petFloatEnabled = Boolean(enabled);
  writeSettings(settings);
  if (!settings.petFloatEnabled) {
    hidePetWindow();
  } else if (mainWindow?.isMinimized()) {
    showPetWindow();
  }
  return settings.petFloatEnabled;
}

function registerIpcHandlers() {
  ipcMain.handle("desktopmate:get-agent-service-url", () => agentService.serviceUrl);
  ipcMain.handle("desktopmate:get-agent-auth-token", () => agentService.authToken);
  ipcMain.handle("desktopmate:get-pet-float-enabled", () => getPetFloatEnabled());
  ipcMain.handle("desktopmate:set-pet-float-enabled", (_event, enabled) =>
    setPetFloatEnabled(Boolean(enabled))
  );
  ipcMain.handle("desktopmate:restore-main-window", () => {
    restoreMainWindow();
  });
  ipcMain.handle("desktopmate:set-pet-window-mode", (_event, mode) => {
    setPetWindowMode(mode === "conversation" ? "conversation" : "compact");
  });
  ipcMain.handle("desktopmate:move-pet-window", (_event, deltaX, deltaY) => {
    movePetWindowByDelta(Number(deltaX) || 0, Number(deltaY) || 0);
  });
  ipcMain.handle("desktopmate:speak-pet-reply", (_event, text) => speakPetReply(text));
  ipcMain.handle("desktopmate:stop-pet-speech", () => stopPetSpeech());
  ipcMain.handle("desktopmate:show-item-in-folder", (_event, targetPath) => {
    const resolvedPath = String(targetPath || "");
    if (!resolvedPath || !fs.existsSync(resolvedPath)) {
      return false;
    }
    shell.showItemInFolder(resolvedPath);
    return true;
  });
  ipcMain.handle("desktopmate:select-directory", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "选择项目文件夹",
      properties: ["openDirectory", "createDirectory"]
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    const selectedPath = result.filePaths[0];
    return {
      path: selectedPath,
      name: path.basename(selectedPath)
    };
  });
}

app.whenReady().then(() => {
  if (process.platform === "darwin" && app.dock) {
    app.dock.setIcon(appIconPath);
  }

  // 优先拉起子进程，再注册 IPC（renderer onMounted 会等待就绪）。
  agentService
    .start()
    .then(() => {
      registerIpcHandlers();
      createWindow();
    })
    .catch((error) => {
      console.error("Failed to start agent service:", error.message);
      registerIpcHandlers();
      createWindow();
    });

  app.on("activate", () => {
    hidePetWindow();
    if (!mainWindow) {
      createWindow();
    } else if (mainWindow.isMinimized()) {
      restoreMainWindow();
    } else {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  isQuitting = true;
  hidePetWindow();
});

app.on("will-quit", () => {
  agentService.stop();
});
