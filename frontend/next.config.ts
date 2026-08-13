import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 产出自带 server.js 的最小运行目录，容器镜像不必装全量 node_modules
  output: "standalone",
};

export default nextConfig;
