/**
 * 初始化租户与管理员账号。
 *
 * 用法（在 frontend/ 目录）：
 *   npx tsx prisma/seed.ts
 *   npx tsx prisma/seed.ts --slug=demo --name="演示大学" --admin=10001 --password=xxxxxxxx
 *
 * 幂等：重复执行只更新学校名，不会重置已有管理员的密码。
 */
import { config } from "dotenv";
config({ path: [".env.local", ".env"] });

import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "../app/generated/prisma/client";
import { hash } from "@node-rs/argon2";

const DEFAULTS = {
  slug: "wtli",
  name: "武汉文理学院",
  admin: "admin001",
  password: "campus2026",
} as const;

function arg(key: keyof typeof DEFAULTS): string {
  const found = process.argv.find((item) => item.startsWith(`--${key}=`));
  return found ? found.slice(key.length + 3) : DEFAULTS[key];
}

async function main(): Promise<void> {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("缺少 DATABASE_URL");

  const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString }) });
  const [slug, name, adminNo, password] = [arg("slug"), arg("name"), arg("admin"), arg("password")];

  try {
    const tenant = await prisma.tenant.upsert({
      where: { slug },
      update: { name },
      create: { slug, name },
    });
    console.log(`租户就绪: ${tenant.slug} (${tenant.name})`);

    const existing = await prisma.user.findUnique({
      where: { tenantId_studentNo: { tenantId: tenant.id, studentNo: adminNo } },
    });
    if (existing) {
      console.log(`管理员已存在，保留原密码: ${adminNo}`);
      return;
    }

    await prisma.user.create({
      data: {
        tenantId: tenant.id,
        studentNo: adminNo,
        name: "系统管理员",
        role: "ADMIN",
        passwordHash: await hash(password, { memoryCost: 19456, timeCost: 2, parallelism: 1 }),
      },
    });
    console.log(`管理员已创建: ${adminNo} / ${password}（请尽快修改）`);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
