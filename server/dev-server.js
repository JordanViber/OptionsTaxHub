const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const serverDir = __dirname;
const repoRoot = path.resolve(serverDir, "..");
const isWin = process.platform === "win32";
const shouldReload = process.argv.includes("--reload");

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const content = fs.readFileSync(filePath, "utf8");
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith("\"") && value.endsWith("\"")) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

loadEnvFile(path.join(serverDir, ".env.local"));
loadEnvFile(path.join(serverDir, ".env"));

// Local API port: BACKEND_PORT, else PORT (env/.env.local), else 8011.
// Production on Render uses uvicorn --port $PORT and does not run this script.
const backendPort = process.env.BACKEND_PORT || process.env.PORT || "8011";

function resolvePython() {
  const candidates = [
    path.join(repoRoot, ".venv", isWin ? "Scripts/python.exe" : "bin/python"),
    path.join(serverDir, ".venv", isWin ? "Scripts/python.exe" : "bin/python"),
    isWin ? "python.exe" : "python3",
    "python",
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return isWin ? "python.exe" : "python3";
}

function stopExistingDevServers() {
  if (isWin) {
    const escapedServerDir = serverDir.replaceAll("\\", "\\\\");
    const script = [
      "$targets = Get-CimInstance Win32_Process | Where-Object {",
      "  $_.Name -eq 'python.exe' -and",
      "  $_.CommandLine -match 'uvicorn' -and",
      `  $_.CommandLine -match '${escapedServerDir}'`,
      "}",
      "foreach ($proc in $targets) {",
      "  Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue",
      "}",
    ].join("; ");

    spawnSync("powershell.exe", ["-NoProfile", "-Command", script], {
      stdio: "ignore",
    });
    return;
  }

  spawnSync("pkill", ["-f", `uvicorn main:app.*${serverDir}`], {
    stdio: "ignore",
  });
}

const python = resolvePython();
stopExistingDevServers();

const uvicornArgs = [
  "-m",
  "uvicorn",
  "main:app",
  "--host",
  "0.0.0.0",
  "--port",
  backendPort,
  "--app-dir",
  serverDir,
];

if (shouldReload) {
  uvicornArgs.push("--reload");
}

const child = spawn(python, uvicornArgs, {
  cwd: serverDir,
  stdio: "inherit",
  env: process.env,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
