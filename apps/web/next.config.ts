import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@trust/ui"],
  poweredByHeader: false,
};

export default nextConfig;
