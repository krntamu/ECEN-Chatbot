/**
 * Pick the real client IP out of X-Forwarded-For.
 *
 * The header is a list that each proxy APPENDS to, and the client controls
 * whatever it sends first. Cloud Run's front end appends the address it
 * actually saw, so a request sent with `X-Forwarded-For: 1.2.3.4` arrives as
 * `1.2.3.4, <real ip>`. Reading the FIRST entry (what this app did before)
 * hands the rate-limit key to the caller: rotate a fake value per request and
 * the 10/minute limit never applies.
 *
 * The trustworthy entry is the one added by the nearest proxy we control,
 * counted from the right. TRUSTED_PROXY_HOPS is how many such proxies sit in
 * front of this server: 1 for Cloud Run alone (default), 2 if a Google load
 * balancer is added in front of it.
 */
export function clientIpFromXff(xff: string | null | undefined, hops = trustedHops()): string {
  const parts = (xff ?? "").split(",").map(p => p.trim()).filter(Boolean);
  if (parts.length === 0) return "unknown";
  const idx = Math.max(0, parts.length - Math.max(1, hops));
  return parts[idx];
}

function trustedHops(): number {
  const n = parseInt(process.env.TRUSTED_PROXY_HOPS ?? "1", 10);
  return Number.isFinite(n) && n > 0 ? n : 1;
}
