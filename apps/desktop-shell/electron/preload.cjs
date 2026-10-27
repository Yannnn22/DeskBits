const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopMate", {
  getAgentServiceUrl: () => ipcRenderer.invoke("desktopmate:get-agent-service-url"),
  getAgentAuthToken: () => ipcRenderer.invoke("desktopmate:get-agent-auth-token"),
  getPetFloatEnabled: () => ipcRenderer.invoke("desktopmate:get-pet-float-enabled"),
  setPetFloatEnabled: (enabled) => ipcRenderer.invoke("desktopmate:set-pet-float-enabled", enabled),
  restoreMainWindow: () => ipcRenderer.invoke("desktopmate:restore-main-window"),
  setPetWindowMode: (mode) => ipcRenderer.invoke("desktopmate:set-pet-window-mode", mode),
  movePetWindow: (deltaX, deltaY) => ipcRenderer.invoke("desktopmate:move-pet-window", deltaX, deltaY),
  speakPetReply: (text) => ipcRenderer.invoke("desktopmate:speak-pet-reply", text),
  stopPetSpeech: () => ipcRenderer.invoke("desktopmate:stop-pet-speech"),
  showItemInFolder: (targetPath) => ipcRenderer.invoke("desktopmate:show-item-in-folder", targetPath),
  selectDirectory: () => ipcRenderer.invoke("desktopmate:select-directory")
});
