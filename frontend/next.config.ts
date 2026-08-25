import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    // Same-origin API proxy so the browser never needs to reach the backend host directly.
    // Destination resolved at build time (baked into standalone server config).
    return [
      {
        source: "/api/v1/:path*",
        destination: `${process.env.METRO_API_INTERNAL ?? "http://127.0.0.1:8084"}/api/v1/:path*`,
      },
    ];
  },
};
export default nextConfig;
