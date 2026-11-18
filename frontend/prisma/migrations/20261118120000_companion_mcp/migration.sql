-- 情感陪伴 + MCP 减压闭环：微待办拆解、脱敏情绪轨迹手账、减压伴学舱状态与企微主动回访开关

ALTER TABLE "im_bindings"
    ADD COLUMN "companionFollowup" BOOLEAN NOT NULL DEFAULT true;

-- 1. 压力拆解微待办
CREATE TABLE "companion_todos" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "detail" TEXT NOT NULL DEFAULT '',
    "estMinutes" INTEGER NOT NULL DEFAULT 20,
    "priority" TEXT NOT NULL DEFAULT 'medium',
    "dueLabel" TEXT NOT NULL DEFAULT '',
    "source" TEXT NOT NULL DEFAULT 'mcp_decompose',
    "status" TEXT NOT NULL DEFAULT 'pending',
    "completedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "companion_todos_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "companion_todos_tenantId_userId_status_createdAt_idx"
    ON "companion_todos"("tenantId", "userId", "status", "createdAt" DESC);

ALTER TABLE "companion_todos" ADD CONSTRAINT "companion_todos_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "companion_todos" ADD CONSTRAINT "companion_todos_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- 2. 脱敏情绪轨迹手账与跨天主动回访队列
CREATE TABLE "mood_journals" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "mood" TEXT NOT NULL,
    "energy" INTEGER NOT NULL DEFAULT 5,
    "stressor" TEXT NOT NULL DEFAULT '日常',
    "summary" TEXT NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'mcp_journal',
    "followupAt" TIMESTAMP(3),
    "followupNote" TEXT NOT NULL DEFAULT '',
    "followupSent" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "mood_journals_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "mood_journals_tenantId_userId_createdAt_idx"
    ON "mood_journals"("tenantId", "userId", "createdAt" DESC);
CREATE INDEX "mood_journals_followupSent_followupAt_idx"
    ON "mood_journals"("followupSent", "followupAt");

ALTER TABLE "mood_journals" ADD CONSTRAINT "mood_journals_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mood_journals" ADD CONSTRAINT "mood_journals_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- 3. 减压伴学舱实时状态
CREATE TABLE "companion_states" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "ambientSound" TEXT NOT NULL DEFAULT 'none',
    "breathingMode" TEXT NOT NULL DEFAULT 'none',
    "focusMinutes" INTEGER NOT NULL DEFAULT 25,
    "nightMode" BOOLEAN NOT NULL DEFAULT false,
    "petMood" TEXT NOT NULL DEFAULT 'normal',
    "note" TEXT NOT NULL DEFAULT '',
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "companion_states_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "companion_states_tenantId_userId_key"
    ON "companion_states"("tenantId", "userId");

ALTER TABLE "companion_states" ADD CONSTRAINT "companion_states_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "companion_states" ADD CONSTRAINT "companion_states_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
