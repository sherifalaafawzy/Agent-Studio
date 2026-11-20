import type { NextConfig } from "next";

// Static export: the Electron shell serves ./out; in dev, `next dev` is used.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: { root: __dirname },
  agentRules: false,
};

export default nextConfig;
