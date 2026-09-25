const nextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  assetPrefix: "/static/console-next",
  images: { unoptimized: true },
  generateBuildId: async () => "console-next-static",
  poweredByHeader: false,
};

export default nextConfig;
