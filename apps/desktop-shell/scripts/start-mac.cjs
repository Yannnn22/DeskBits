const { spawnSync } = require("node:child_process");
const path = require("node:path");

function getElectronAppPath() {
  const electronExecutable = require("electron");
  const marker = `${path.sep}Contents${path.sep}MacOS${path.sep}Electron`;

  if (typeof electronExecutable !== "string" || !electronExecutable.endsWith(marker)) {
    throw new Error(`Unexpected Electron executable path: ${electronExecutable}`);
  }

  return electronExecutable.slice(0, -marker.length);
}

const electronApp = getElectronAppPath();
const appRoot = path.resolve(__dirname, "..");
const passthroughArgs = process.argv.slice(2);
const result = spawnSync("open", ["-n", electronApp, "--args", appRoot, ...passthroughArgs], {
  stdio: "inherit"
});

if (result.error) {
  throw result.error;
}

process.exit(result.status ?? 0);
