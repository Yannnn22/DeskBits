// DesktopMate Agent Service 子进程管理器。
// 负责：探测 Python 环境 → spawn apps/agent-service/run.py → 解析 stdout 的
// desktopmate_ready JSON（port + auth_token）→ 供 renderer 使用；应用退出时回收。
//
// 若设置了 DESKTOPMATE_AGENT_SERVICE_URL / DESKTOPMATE_AGENT_AUTH_TOKEN，
// 则直接使用外部服务，不再拉起子进程。

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const { createInterface } = require("node:readline");
const path = require("node:path");

const READY_TIMEOUT_MS = 20_000;

function getAgentServiceRoot() {
  // 候选顺序：环境变量 > 主仓库 apps/agent-service > 开发 worktree > 相对路径
  const candidates = [
    process.env.DESKTOPMATE_AGENT_SERVICE_DIR,
    path.resolve(__dirname, "..", "..", "agent-service"),
    path.resolve(__dirname, "..", "..", "..", ".worktrees", "agent-core-slice", "apps", "agent-service")
  ].filter(Boolean);
  const found = candidates.find((candidate) => {
    try {
      return require("node:fs").existsSync(path.join(candidate, "run.py"));
    } catch {
      return false;
    }
  });
  return found || candidates[0];
}

function getPythonCommand() {
  const configured = process.env.DESKTOPMATE_PYTHON;
  if (configured) {
    return { command: configured, args: [] };
  }
  const venvPython = path.join(getAgentServiceRoot(), ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return { command: venvPython, args: [] };
  }
  // 开发机尚未创建项目 venv 时，由 uv 使用 pyproject.lock 中的受控环境启动。
  return { command: "uv", args: ["run", "python"] };
}

class AgentServiceManager {
  constructor() {
    this.child = null;
    this.port = null;
    this.token = null;
    this.baseUrl = process.env.DESKTOPMATE_AGENT_SERVICE_URL || null;
    this.overrideToken = process.env.DESKTOPMATE_AGENT_AUTH_TOKEN || "";
    this.startPromise = null;
  }

  get serviceUrl() {
    if (this.baseUrl) {
      return this.baseUrl.replace(/\/+$/, "");
    }
    return this.port ? `http://127.0.0.1:${this.port}` : null;
  }

  get authToken() {
    return this.baseUrl ? this.overrideToken : this.token;
  }

  isManaged() {
    return !this.baseUrl;
  }

  start() {
    if (this.baseUrl) {
      // 外部服务模式：无需子进程
      return Promise.resolve({ url: this.serviceUrl, token: this.authToken });
    }
    if (this.startPromise) {
      return this.startPromise;
    }
    this.startPromise = this._spawnAndWait();
    return this.startPromise;
  }

  _spawnAndWait() {
    return new Promise((resolve, reject) => {
      const { command, args } = getPythonCommand();
      const cwd = getAgentServiceRoot();
      const env = {
        ...process.env,
        DESKTOPMATE_HOST: "127.0.0.1",
        DESKTOPMATE_PORT: "0",
        // Electron 加载本地文件（file://），页面 Origin 序列化为 "null"。
        // 必须放行，否则 renderer 的 HTTP/WebSocket 请求会被后端 CORS/Origin 校验拒绝。
        DESKTOPMATE_ALLOWED_ORIGINS: "null,file://"
      };
      const child = spawn(command, [...args, "run.py"], {
        cwd,
        env,
        stdio: ["ignore", "pipe", "inherit"]
      });
      this.child = child;

      const timer = setTimeout(() => {
        reject(new Error("Agent service did not become ready in time"));
        this._stopChild();
      }, READY_TIMEOUT_MS);

      const lines = createInterface({ input: child.stdout });
      lines.on("line", (line) => {
        const trimmed = line.trim();
        if (!trimmed.startsWith("{")) {
          return;
        }
        let payload;
        try {
          payload = JSON.parse(trimmed);
        } catch {
          return;
        }
        if (payload.event === "desktopmate_ready" && typeof payload.port === "number") {
          this.port = payload.port;
          this.token = payload.auth_token || "";
          clearTimeout(timer);
          lines.close();
          resolve({ url: this.serviceUrl, token: this.authToken });
        }
      });

      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });

      child.on("exit", (code) => {
        if (!this.port) {
          clearTimeout(timer);
          reject(new Error(`Agent service exited early (code ${code})`));
        }
        this.child = null;
        this.port = null;
        this.token = null;
      });
    });
  }

  stop() {
    this._stopChild();
  }

  _stopChild() {
    if (!this.child) {
      return;
    }
    const child = this.child;
    this.child = null;
    child.kill("SIGTERM");
    // 给优雅退出一点时间，之后强制回收
    setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
    }, 2000);
  }
}

module.exports = { AgentServiceManager };
