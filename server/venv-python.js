/**
 * Cross-platform venv Python launcher for npm scripts.
 *
 * Usage: node venv-python.js <python args...>
 *
 * Prefers the local .venv Python so npm scripts always use the project
 * virtualenv instead of whatever system Python is first on PATH.
 * Falls back gracefully to the bare 'python' command if no venv is found
 * (e.g., in CI where Python is configured globally by the runner).
 */
const { spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const repoRoot = path.resolve(__dirname, "..");
const isWin = process.platform === "win32";

// Candidate Python executables in priority order
const candidates = [
  path.join(repoRoot, ".venv", isWin ? "Scripts/python.exe" : "bin/python"),
  path.join(__dirname, ".venv", isWin ? "Scripts/python.exe" : "bin/python"),
  isWin ? "python.exe" : "python3",
  "python",
];

function findPython() {
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  // Fallback to bare name — let the OS resolve it
  return "python";
}

const python = findPython();
const args = process.argv.slice(2);

const result = spawnSync(python, args, { stdio: "inherit", cwd: __dirname });
process.exit(result.status ?? 1);
