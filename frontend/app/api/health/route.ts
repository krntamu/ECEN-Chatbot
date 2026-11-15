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

export async function GET() {
  let ok = false;
  try {
    const res = await fetch(`${BACKEND_URL}/health/deep`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    ok = res.ok;
  } catch {
    ok = false;
  }
  return Response.json(
    { status: ok ? "ok" : "unavailable" },
    { status: ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
