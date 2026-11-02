/**
 * The only way this plugin talks to the engine: spawn the local Python process
 * and read its `--jsonl` stream.
 *
 * Hard rules (docs/design/06-plugin.md §6 and §9):
 *  - stdout is JSON only; human logs go to stderr;
 *  - chunk boundaries are arbitrary, so lines are buffered;
 *  - a timeout kills the whole process tree (taskkill /T /F on win32, SIGTERM
 *    elsewhere) rather than leaving python children behind;
 *  - the plugin never shells out for anything else and never writes files.
 */

import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import type { RunEvent } from "./types";

/** Spawn with an explicit interpreter path; never rely on PATH. */
export interface BridgeOptions {
  pythonPath: string;
  vaultRoot: string;
}

export type BridgeErrorKind = "enoent" | "busy" | "spawn";

export class BridgeError extends Error {
  readonly kind: BridgeErrorKind;
  readonly path?: string;
  readonly code?: string;

  constructor(kind: BridgeErrorKind, message: string, options: { path?: string; code?: string } = {}) {
    super(message);
    this.name = "BridgeError";
    this.kind = kind;
    this.path = options.path;
    this.code = options.code;
  }
}

export interface RunResult {
  code: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  cancelled: boolean;
}

export interface JsonResult extends RunResult {
  json: unknown;
}

const STDERR_CAP = 200_000;
const MAX_PENDING_LINE = 4 * 1024 * 1024;

/**
 * A tiny line splitter. `push` may receive half a line or several lines at
 * once; only complete lines are returned. `flush` releases the final line when
 * the process closes without a trailing newline.
 */
export class LineBuffer {
  private buf = "";

  push(chunk: string): string[] {
    this.buf += chunk;
    if (this.buf.length > MAX_PENDING_LINE) {
      // A runaway line must not eat the whole renderer heap. Drop it.
      this.buf = "";
      return [];
    }
    const lines: string[] = [];
    let index: number;
    while ((index = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, index).replace(/\r$/, "").trim();
      this.buf = this.buf.slice(index + 1);
      if (line) lines.push(line);
    }
    return lines;
  }

  flush(): string[] {
    const line = this.buf.replace(/\r$/, "").trim();
    this.buf = "";
    return line ? [line] : [];
  }
}

/** Parse one stdout line; non-JSON lines are ignored (never fatal). */
export function parseJsonLine(line: string): RunEvent | null {
  if (!line) return null;
  try {
    const value = JSON.parse(line) as unknown;
    if (value && typeof value === "object" && typeof (value as RunEvent).event === "string") {
      return value as RunEvent;
    }
    return null;
  } catch {
    return null;
  }
}

export class Bridge {
  private child?: ChildProcessWithoutNullStreams;
  private timedOut = false;
  private cancelRequested = false;

  constructor(private readonly options: BridgeOptions) {}

  get busy(): boolean {
    return !!this.child;
  }

  get pid(): number | undefined {
    return this.child?.pid;
  }

  /**
   * Run a streaming command. `args` are the subcommand and its own flags; the
   * bridge always appends `--vault <vaultRoot> --jsonl`.
   */
  run(
    args: string[],
    timeoutMs: number,
    onEvent: (event: RunEvent) => void,
  ): Promise<RunResult> {
    return this.exec(args, timeoutMs, onEvent, []);
  }

  /**
   * Run a one-shot JSON command (`--version --json`, `doctor --json`). The
   * `--jsonl` flag is still passed (the engine ignores it for these commands);
   * stdout is collected and parsed as one document instead of as events.
   */
  async probeJson(args: string[], timeoutMs: number): Promise<JsonResult> {
    const result = await this.exec(args, timeoutMs, null, []);
    return { ...result, json: parseJsonDocument(result.stdout) };
  }

  /**
   * Ask the running child to stop. The promise returned by `run` resolves once
   * the `close` event arrives, so callers can still update the panel.
   */
  cancel(): void {
    this.cancelRequested = true;
    this.kill();
  }

  kill(): void {
    const child = this.child;
    if (!child) return;
    try {
      if (process.platform === "win32" && child.pid) {
        // Killing only python leaves its worker/HTTP children behind.
        spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
      } else {
        child.kill("SIGTERM");
      }
    } catch {
      // The process may have exited between the check and the signal.
    }
  }

  private exec(
    args: string[],
    timeoutMs: number,
    onEvent: ((event: RunEvent) => void) | null,
    extraArgs: string[],
  ): Promise<RunResult> {
    if (this.child) {
      return Promise.reject(new BridgeError("busy", "已有任务在运行"));
    }

    const cwd = this.options.vaultRoot || process.cwd();
    const argv = [
      "-m",
      "litradar",
      ...args,
      ...extraArgs,
      "--vault",
      cwd,
      // The JSONL contract is uniform: every spawn carries it. Probes print a
      // single JSON document, which is parsed from the collected stdout.
      "--jsonl",
    ];

    this.timedOut = false;
    this.cancelRequested = false;

    return new Promise<RunResult>((resolve, reject) => {
      let settled = false;
      let stdout = "";
      let stderr = "";
      const lines = new LineBuffer();
      let timer: ReturnType<typeof setTimeout> | undefined;

      const child = spawn(this.options.pythonPath, argv, {
        cwd,
        windowsHide: true,
        env: {
          ...process.env,
          PYTHONIOENCODING: "utf-8",
          PYTHONUNBUFFERED: "1",
        },
      });
      this.child = child;

      const cleanup = (): void => {
        if (timer) clearTimeout(timer);
        timer = undefined;
        if (this.child === child) this.child = undefined;
      };

      const feed = (chunk: string): void => {
        stdout += chunk;
        for (const line of lines.push(chunk)) {
          if (!onEvent) continue;
          const event = parseJsonLine(line);
          if (event) onEvent(event);
        }
        if (stdout.length > STDERR_CAP) stdout = stdout.slice(-STDERR_CAP);
      };

      timer = setTimeout(() => {
        this.timedOut = true;
        this.kill();
      }, Math.max(1000, timeoutMs));

      child.stdout.setEncoding("utf-8");
      child.stdout.on("data", (chunk: string) => feed(chunk));

      child.stderr.setEncoding("utf-8");
      child.stderr.on("data", (chunk: string) => {
        stderr += chunk;
        if (stderr.length > STDERR_CAP) stderr = stderr.slice(-STDERR_CAP);
      });

      child.on("error", (error: NodeJS.ErrnoException) => {
        if (settled) return;
        settled = true;
        cleanup();
        // At this point no `close` will arrive.
        if (error.code === "ENOENT") {
          reject(
            new BridgeError("enoent", `找不到 python：${this.options.pythonPath}`, {
              path: this.options.pythonPath,
              code: error.code,
            }),
          );
          return;
        }
        reject(
          new BridgeError("spawn", `无法启动引擎：${error.message}`, {
            path: this.options.pythonPath,
            code: error.code,
          }),
        );
      });

      child.on("close", (code: number | null, signal: NodeJS.Signals | null) => {
        if (settled) return;
        settled = true;
        cleanup();
        // A process that exits without a trailing newline still has one event
        // worth parsing.
        if (onEvent) {
          for (const line of lines.flush()) {
            const event = parseJsonLine(line);
            if (event) onEvent(event);
          }
        }
        resolve({
          code,
          signal,
          stdout,
          stderr,
          timedOut: this.timedOut,
          cancelled: this.cancelRequested,
        });
      });
    });
  }
}

/** Parse the stdout of `--version --json` / `doctor --json`. */
export function parseJsonDocument(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    // Defensive: tolerate a leading log line, parse the last JSON-looking line.
    const lines = trimmed.split(/\r?\n/).filter((line) => line.trim().startsWith("{"));
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      try {
        return JSON.parse(lines[i]);
      } catch {
        // keep trying
      }
    }
    return null;
  }
}
