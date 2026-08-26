import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "@/app/generated/prisma/client";

// 开发模式下 Next 的热更新会反复求值模块，不缓存的话每次都新建连接池，
// 很快就会把 Postgres 的连接数吃满。挂到 globalThis 上复用同一个实例。
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

let client: PrismaClient | undefined;

function createClient(): PrismaClient {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("缺少 DATABASE_URL，无法连接数据库");
  }
  return new PrismaClient({ adapter: new PrismaPg({ connectionString }) });
}

/**
 * 首次真正查询时才建连接池。
 * next build 会求值路由模块来收集配置，而镜像构建阶段没有 DATABASE_URL，
 * 在模块顶层急切初始化会让构建直接失败。
 */
export function getPrisma(): PrismaClient {
  if (client) return client;
  client = globalForPrisma.prisma ?? createClient();
  if (process.env.NODE_ENV !== "production") {
    globalForPrisma.prisma = client;
  }
  return client;
}
