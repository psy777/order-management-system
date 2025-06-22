import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["http://192.168.1.30:3000", "https://88de-98-97-6-90.ngrok-free.app"],
};

export default nextConfig;
