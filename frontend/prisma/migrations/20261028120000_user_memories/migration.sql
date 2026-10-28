-- 长期记忆迁到 Postgres：Redis 里那 80 条「可丢」的数据，变成有权威副本的学生档案。
-- embedding 存 double precision[]：每人最多 80 条，检索时在进程内算余弦就够了，
-- 不引 pgvector 扩展（2C2G 上少一个组件少一份运维）。
CREATE TABLE "user_memories" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "kind" TEXT NOT NULL DEFAULT 'fact',
    "slot" TEXT NOT NULL DEFAULT '',
    "origin" TEXT NOT NULL DEFAULT 'manual',
    "pinned" BOOLEAN NOT NULL DEFAULT false,
    "sensitive" BOOLEAN NOT NULL DEFAULT false,
    "embedding" DOUBLE PRECISION[] DEFAULT ARRAY[]::DOUBLE PRECISION[],
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "user_memories_pkey" PRIMARY KEY ("id")
);

-- 一次检索只读一个人的全部记忆，这个组合索引就够了
CREATE INDEX "user_memories_tenantId_userId_idx" ON "user_memories"("tenantId", "userId");

ALTER TABLE "user_memories" ADD CONSTRAINT "user_memories_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "user_memories" ADD CONSTRAINT "user_memories_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
