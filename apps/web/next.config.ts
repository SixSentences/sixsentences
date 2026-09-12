import type { NextConfig } from "next";

// Defense-in-depth headers for the app origin (the bearer token lives in
// localStorage here). We deliberately avoid script-src/connect-src directives:
// Next.js hydration and the configurable API origin would need nonce plumbing
// and per-deploy origins, and a wrong value silently breaks the app. The
// directives below are safe regardless of deploy and cost nothing.
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "X-Robots-Tag",
    value: "noindex, nofollow, noarchive, nosnippet",
  },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(self), geolocation=(), payment=(), usb=()",
  },
  {
    key: "Cross-Origin-Opener-Policy",
    value: "same-origin-allow-popups",
  },
  {
    key: "Content-Security-Policy",
    value: [
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "object-src 'none'",
      "form-action 'self'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  // Caddy also strips this response header as defense in depth. Keeping the
  // framework setting explicit protects direct standalone deployments too.
  poweredByHeader: false,
  // Registration reads the API's runtime gate; recovery is always available
  // to existing users. Build-time redirects would make an admin toggle stale.
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
