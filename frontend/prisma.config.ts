// dotenv 默认只读 .env，而 Next.js 用的是 .env.local。
// 这里按 Next 的优先级把两个都加载（先命中的不会被后面覆盖）。
import { config } from "dotenv";
config({ path: [".env.local", ".env"] });

import { defineConfig, env } from "prisma/config";

export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
  },
  datasource: {
    url: env("DATABASE_URL"),
  },
});
