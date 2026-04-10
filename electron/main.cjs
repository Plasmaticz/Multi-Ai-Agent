const { app, BrowserWindow, Menu, dialog, ipcMain } = require("electron");
const path = require("path");
const net = require("net");
const { spawn } = require("child_process");

let mainWindow = null;
let backendProcess = null;
let backendUrl = null;

function createMenu() {
  const template = [
    {
      label: "AI Agent Desktop",
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "quit" }
      ]
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" }
      ]
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 760,
    title: "AI Agent Desktop",
    backgroundColor: "#efe7da",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
    if (!app.isQuitting) {
      app.quit();
    }
  });
}

async function startBackend() {
  const explicitUrl = process.env.ELECTRON_START_URL;
  if (explicitUrl) {
    backendUrl = explicitUrl;
    return;
  }

  const port = await findAvailablePort([8000, 8001, 8002, 8003, 8004]);
  const host = "127.0.0.1";
  backendUrl = `http://${host}:${port}`;
  const appDataDir = path.join(app.getPath("userData"), "data");

  const env = {
    ...process.env,
    APP_HOST: host,
    APP_PORT: String(port),
    APP_DATA_DIR: appDataDir,
    ENVIRONMENT: "desktop"
  };

  if (app.isPackaged) {
    const backendBinary = path.join(process.resourcesPath, "backend", "ai-agent-backend");
    backendProcess = spawn(backendBinary, ["--host", host, "--port", String(port), "--app-data-dir", appDataDir, "--environment", "desktop"], {
      env
    });
  } else {
    backendProcess = spawn("python3", ["-m", "app.server", "--host", host, "--port", String(port), "--app-data-dir", appDataDir, "--environment", "desktop"], {
      cwd: path.resolve(__dirname, ".."),
      env
    });
  }

  backendProcess.stdout.on("data", (data) => {
    process.stdout.write(`[backend] ${data}`);
  });
  backendProcess.stderr.on("data", (data) => {
    process.stderr.write(`[backend] ${data}`);
  });
  backendProcess.on("exit", (code) => {
    if (code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
      showFatalError(`Backend exited unexpectedly with code ${code ?? "unknown"}.`);
    }
  });

  await waitForHealth(backendUrl);
}

async function findAvailablePort(candidates) {
  for (const port of candidates) {
    const available = await new Promise((resolve) => {
      const server = net.createServer();
      server.unref();
      server.on("error", () => resolve(false));
      server.listen({ port, host: "127.0.0.1" }, () => {
        server.close(() => resolve(true));
      });
    });
    if (available) {
      return port;
    }
  }
  throw new Error("No available localhost port found for the backend.");
}

async function waitForHealth(url) {
  const timeoutMs = 30000;
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(`${url}/health`);
      if (response.ok) {
        return;
      }
    } catch (error) {
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error("Backend did not become healthy within 30 seconds.");
}

function showFatalError(message) {
  const errorHtml = `
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px;background:#f3efe5;color:#1f2937;">
        <h1 style="margin-top:0;">AI Agent Desktop could not start</h1>
        <p>${message}</p>
        <p>Check the terminal output for backend startup logs.</p>
      </body>
    </html>
  `;
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(errorHtml)}`);
}

function stopBackend() {
  if (!backendProcess) {
    return;
  }
  const child = backendProcess;
  backendProcess = null;
  child.kill("SIGTERM");
  setTimeout(() => {
    if (!child.killed) {
      child.kill("SIGKILL");
    }
  }, 3000);
}

ipcMain.handle("dialog:choose-workspace-directory", async () => {
  const ownerWindow = BrowserWindow.getFocusedWindow() || mainWindow || undefined;
  const result = await dialog.showOpenDialog(ownerWindow, {
    properties: ["openDirectory"],
    title: "Choose Workspace Folder"
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  return result.filePaths[0];
});

app.whenReady().then(async () => {
  createMenu();
  createWindow();

  try {
    await startBackend();
    await mainWindow.loadURL(backendUrl);
  } catch (error) {
    showFatalError(error.message);
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      if (backendUrl) {
        mainWindow.loadURL(backendUrl);
      }
    }
  });
});

app.on("before-quit", () => {
  app.isQuitting = true;
  stopBackend();
});

app.on("will-quit", () => {
  stopBackend();
});

app.on("window-all-closed", () => {
  app.quit();
});
