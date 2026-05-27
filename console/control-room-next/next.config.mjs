/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  basePath: "/control-room",
  assetPrefix: "/control-room",
  trailingSlash: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  generateBuildId: async () => "control-room-static",
};

export default nextConfig;
