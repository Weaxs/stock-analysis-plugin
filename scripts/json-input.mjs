import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Large reports/K-lines exceed Windows argv limits. Remove transport files even on failure.
export async function withJsonInput(input, execute) {
  let directory;
  let result;
  try {
    directory = await mkdtemp(join(tmpdir(), "stock-analysis-"));
    const path = join(directory, "input.json");
    await writeFile(path, JSON.stringify(input), { encoding: "utf8", mode: 0o600 });
    result = await execute(path);
  } catch {
    result = JSON.stringify({ error: "Analysis command failed; check input and interpreter/dependencies" });
  } finally {
    if (directory) {
      try {
        await rm(directory, { recursive: true, force: true });
      } catch {
        result = JSON.stringify({ error: "Could not remove temporary analysis input" });
      }
    }
  }
  return result;
}
