import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["http://192.168.1.30:3000"],
};

export default nextConfig;
