// Deploy v2/frontend/dist to the `gh-pages` branch via a temporary git
// worktree. Avoids the `gh-pages` npm package, which silently inherits
// the parent repo's .gitignore (excluding our data/*.json + *.bin) and
// also commits its own cache directory back into the branch.
//
// Run from v2/frontend: `node scripts/deploy.mjs`.

import { execSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, cpSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

function run(cmd, opts = {}) {
  console.log(`> ${cmd}`);
  return execSync(cmd, { stdio: "inherit", ...opts });
}

function captureRun(cmd, opts = {}) {
  return execSync(cmd, { encoding: "utf8", ...opts }).trim();
}

const frontend = resolve(import.meta.dirname, "..");
const distDir = join(frontend, "dist");

if (!existsSync(distDir)) {
  console.error(`dist not found at ${distDir}. Run \`npm run build\` first.`);
  process.exit(1);
}

const repoRoot = captureRun("git rev-parse --show-toplevel", { cwd: frontend });
const branch = "gh-pages";
const workDir = mkdtempSync(join(tmpdir(), "gh-pages-deploy-"));

console.log(`Repo root: ${repoRoot}`);
console.log(`Worktree:  ${workDir}`);
console.log(`Branch:    ${branch}`);

try {
  // Drop any existing local gh-pages so the orphan worktree starts clean.
  try {
    run(`git worktree remove --force "${workDir}"`, { cwd: repoRoot, stdio: "ignore" });
  } catch {
    // worktree didn't exist; fine
  }
  try {
    run(`git branch -D ${branch}`, { cwd: repoRoot, stdio: "ignore" });
  } catch {
    // local branch didn't exist; fine
  }

  run(`git worktree add --orphan -b ${branch} "${workDir}"`, { cwd: repoRoot });

  // Wipe whatever the orphan branch inherited (nothing for a fresh orphan,
  // but be defensive) and copy dist contents in.
  for (const entry of readdirSync(workDir)) {
    if (entry === ".git") continue;
    rmSync(join(workDir, entry), { recursive: true, force: true });
  }
  cpSync(distDir, workDir, { recursive: true });

  run(`git add -A`, { cwd: workDir });
  run(`git -c user.email=deploy@local -c user.name=deploy commit -m "Deploy v2 build"`, {
    cwd: workDir,
  });
  run(`git push --force origin ${branch}`, { cwd: workDir });

  console.log("Published to origin/gh-pages.");
} finally {
  try {
    run(`git worktree remove --force "${workDir}"`, { cwd: repoRoot, stdio: "ignore" });
  } catch {
    // ignore
  }
}
