/**
 * API route: GET /api/health
 *
 * The only public port serves Next.js; FastAPI listens on an internal port, so
 * its /health/deep check (database reachable, index non-empty, embedder
 * matches) could not be reached by an external uptime monitor. The service
 * could be up, serving the page, and unable to answer a single question with
 * nothing outside the container able to tell.
 *
 * This exposes that check as a plain up/down signal: 200 when the backend can
 * answer questions, 503 otherwise. The backend's detail (row counts, error
 * text) is deliberately not forwarded.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";

// This endpoint is public and each uncached call costs a database round trip,
// so the result is reused briefly. An uptime monitor polls every few minutes;
// anyone hammering it gets the cached answer instead of a query per request.
const CACHE_MS = 15_000;
let cached: { ok: boolean; at: number } | null = null;

async function probe(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/health/deep`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function GET() {
  const now = Date.now();
  if (!cached || now - cached.at > CACHE_MS) {
    cached = { ok: await probe(), at: now };
  }
  const ok = cached.ok;
  return Response.json(
    { status: ok ? "ok" : "unavailable" },
    { status: ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
