// Some model gateways resolve through Chinese DNS but not the runner's resolver.
// Keep the original hostname for HTTPS; only supply its missing A record.
import { execFileSync } from "node:child_process";
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

if (process.env.OPENAI_BASE_URL) {
  const host = new URL(process.env.OPENAI_BASE_URL).hostname;
  let resolved = false;
  try {
    await lookup(host, { family: 4 });
    resolved = true;
  } catch { /* ask a second resolver below */ }

  if (!resolved) {
    const url = `https://dns.alidns.com/resolve?name=${encodeURIComponent(host)}&type=A`;
    const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!response.ok) throw new Error(`AliDNS lookup failed: HTTP ${response.status}`);
    const answer = (await response.json()).Answer?.find((record) => record.type === 1);
    if (isIP(answer?.data) !== 4) throw new Error(`No IPv4 address found for ${host}`);
    execFileSync("sudo", ["tee", "-a", "/etc/hosts"], {
      input: `${answer.data} ${host}\n`,
      stdio: ["pipe", "ignore", "pipe"],
    });
    await fetch(`https://${host}/`, { signal: AbortSignal.timeout(10_000) });
    console.log(`Resolved ${host} through AliDNS; HTTPS is reachable`);
  }
}
