import { spawn } from "node:child_process";

const env = {
  ...process.env,
  NODE_ENV: process.env.NODE_ENV ?? "development",
};

const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

function runPnpm(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(pnpmCommand, args, {
      stdio: "inherit",
      env,
      shell: process.platform === "win32",
    });

    child.on("error", reject);
    child.on("exit", (code, signal) => {
      if (signal) {
        reject(new Error(`pnpm ${args.join(" ")} exited with signal ${signal}`));
        return;
      }

      if (code !== 0) {
        reject(new Error(`pnpm ${args.join(" ")} exited with code ${code ?? 1}`));
        return;
      }

      resolve();
    });
  });
}

await runPnpm(["run", "build"]);
await runPnpm(["run", "start"]);