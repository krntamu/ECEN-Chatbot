/**
 * Pick the real client IP out of X-Forwarded-For.
 *
 * The header is a list that each proxy APPENDS to, and the client controls
 * whatever it sends first. A request sent with `X-Forwarded-For: 1.2.3.4`
 * arrives as `1.2.3.4, <real ip>[, <google proxy>]`. Reading the FIRST entry
 * (what this app did before) hands the rate-limit key to the caller: rotate a
 * fake value per request and the 10/minute limit never applies.
 *
 * So the list is read from the RIGHT. Entries that are infrastructure rather
 * than a user (private ranges, link-local, Google's load-balancer ranges) are
 * skipped, and the first public address found is the client. That is correct
 * for Cloud Run on its own and for Cloud Run behind a Google load balancer
 * without configuration, and it fails safe: picking a shared proxy address by
 * mistake would put every user in one rate-limit bucket.
 *
 * TRUSTED_PROXY_HOPS (optional) overrides the walk with a fixed position from
 * the right, for a setup with a non-Google proxy in front.
 */
export function clientIpFromXff(xff: string | null | undefined, hops = fixedHops()): string {
  const parts = (xff ?? "").split(",").map(p => p.trim()).filter(Boolean);
  if (parts.length === 0) return "unknown";
  if (hops > 0) return parts[Math.max(0, parts.length - hops)];
  for (let i = parts.length - 1; i >= 0; i--) {
    if (!isInfrastructure(parts[i])) return parts[i];
  }
  // Everything looked internal (local dev, or a private network): the nearest
  // hop is still the best key available.
  return parts[parts.length - 1];
}

function fixedHops(): number {
  const n = parseInt(process.env.TRUSTED_PROXY_HOPS ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function isInfrastructure(ip: string): boolean {
  const v = ip.toLowerCase();
  if (v.includes(":")) {
    // IPv6 loopback, link-local, unique-local; and IPv4-mapped forms.
    if (v === "::1" || v.startsWith("fe80:") || v.startsWith("fc") || v.startsWith("fd")) return true;
    const mapped = v.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
    return mapped ? isInfrastructure(mapped[1]) : false;
  }
  const o = v.split(".").map(Number);
  if (o.length !== 4 || o.some(n => !Number.isInteger(n) || n < 0 || n > 255)) return true; // junk is not a client
  const [a, b] = o;
  return (
    a === 10 || a === 127 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 169 && b === 254) ||
    (a === 100 && b >= 64 && b <= 127) ||   // carrier-grade NAT / internal
    (a === 35 && b === 191) ||              // Google front ends
    (a === 130 && b === 211 && o[2] <= 3)   // Google load balancers 130.211.0.0/22
  );
}
