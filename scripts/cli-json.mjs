// Shared host-side CLI result rules; every adapter must surface failures the same way.
export const failureMessage = (error) =>
  error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" ? "Python command output exceeded buffer limit"
    : error.killed ? "Python command timed out"
      : typeof error.code === "number" ? `Python command failed (exit ${error.code}); check interpreter/dependencies`
        : "Python command failed; check interpreter/dependencies";

// Failed, empty, or non-JSON CLI output becomes a JSON error; a CLI-provided JSON error is kept.
export function cliJson(stdout, failure) {
  try {
    const data = JSON.parse(stdout);
    if (!failure || (data && typeof data.error === "string")) return stdout;
  } catch {
    failure ??= stdout.trim() ? "Python command returned non-JSON output" : "Python command returned no JSON";
  }
  return JSON.stringify({ error: failure });
}

// Explicit null optional args would otherwise reach argv as "null"; dropping them applies defaults.
export const withoutNulls = (params) =>
  params && typeof params === "object" && !Array.isArray(params)
    ? Object.fromEntries(Object.entries(params).filter(([, value]) => value !== null))
    : params;
