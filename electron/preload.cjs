const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopApp", {
  platform: process.platform,
  chooseWorkspaceDirectory: () => ipcRenderer.invoke("dialog:choose-workspace-directory")
});
