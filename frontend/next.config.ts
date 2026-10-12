import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 产出自带 server.js 的最小运行目录，容器镜像不必装全量 node_modules
  output: "standalone",
  async headers() {
    return [
      {
        // 只有 /embed/* 给学校官网用 iframe 嵌；登录页、管理面板一律禁止被嵌，防点击劫持。
        // 挂件页不在这里按域名放行：来源域名在签发访客令牌时校验，没有令牌的挂件页什么也做不了
        source: "/:path((?!embed/).*)",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
