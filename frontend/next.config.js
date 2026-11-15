/** @type {import('next').NextConfig} */

// Who may embed the chat in an <iframe>. Defaults to this origin plus any
// tamu.edu site (so the department can embed it) and nobody else, which blocks
// clickjacking from arbitrary pages. NOTE: read at BUILD time (headers are
// baked into the standalone output), so override it as a build arg, not a
// Cloud Run runtime env var.
const FRAME_ANCESTORS = process.env.FRAME_ANCESTORS || "'self' https://*.tamu.edu";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  // Deliberately narrow CSP: only directives that can't break Next's inline
  // bootstrap scripts. A script-src policy needs per-request nonces.
  {
    key: "Content-Security-Policy",
    value: `frame-ancestors ${FRAME_ANCESTORS}; base-uri 'self'; form-action 'self'; object-src 'none'`,
  },
];

const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  env: {
    BACKEND_URL: process.env.BACKEND_URL || "http://localhost:8000",
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

module.exports = nextConfig;
