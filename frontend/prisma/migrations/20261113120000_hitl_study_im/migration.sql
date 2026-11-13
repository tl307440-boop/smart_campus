-- Phase 2 & Phase 3：HITL 人工求助工单、个人课程伴学空间、企微/QQ 机器人与主动推送偏好

ALTER TABLE "tenant_ai_configs"
    ADD COLUMN "wecomBotId" TEXT NOT NULL DEFAULT '',
    ADD COLUMN "wecomBotSecret" TEXT NOT NULL DEFAULT '',
    ADD COLUMN "wecomBotSecretHint" TEXT NOT NULL DEFAULT '',
    ADD COLUMN "qqOnebotUrl" TEXT NOT NULL DEFAULT '',
    ADD COLUMN "qqOnebotToken" TEXT NOT NULL DEFAULT '',
    ADD COLUMN "qqOnebotTokenHint" TEXT NOT NULL DEFAULT '';

-- 1. HITL 人工求助工单
CREATE TABLE "help_tickets" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "messageId" TEXT NOT NULL DEFAULT '',
    "category" TEXT NOT NULL DEFAULT 'other',
    "question" TEXT NOT NULL,
    "contextSummary" TEXT NOT NULL DEFAULT '',
    "status" TEXT NOT NULL DEFAULT 'open',
    "reply" TEXT NOT NULL DEFAULT '',
    "repliedById" TEXT,
    "repliedAt" TIMESTAMP(3),
    "mergedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "help_tickets_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "help_tickets_tenantId_status_createdAt_idx" ON "help_tickets"("tenantId", "status", "createdAt");
CREATE INDEX "help_tickets_tenantId_userId_createdAt_idx" ON "help_tickets"("tenantId", "userId", "createdAt");

ALTER TABLE "help_tickets" ADD CONSTRAINT "help_tickets_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "help_tickets" ADD CONSTRAINT "help_tickets_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "help_tickets" ADD CONSTRAINT "help_tickets_repliedById_fkey"
    FOREIGN KEY ("repliedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- 2. 个人课程伴学空间
CREATE TABLE "study_courses" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "code" TEXT NOT NULL DEFAULT '',
    "teacher" TEXT NOT NULL DEFAULT '',
    "term" TEXT NOT NULL DEFAULT '',
    "source" TEXT NOT NULL DEFAULT 'manual',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "study_courses_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "study_courses_tenantId_userId_name_key" ON "study_courses"("tenantId", "userId", "name");
CREATE INDEX "study_courses_tenantId_userId_idx" ON "study_courses"("tenantId", "userId");

ALTER TABLE "study_courses" ADD CONSTRAINT "study_courses_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_courses" ADD CONSTRAINT "study_courses_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "study_materials" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "kind" TEXT NOT NULL DEFAULT 'file',
    "filename" TEXT NOT NULL DEFAULT '',
    "content" TEXT NOT NULL,
    "charCount" INTEGER NOT NULL DEFAULT 0,
    "summary" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "study_materials_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "study_materials_tenantId_userId_courseId_idx" ON "study_materials"("tenantId", "userId", "courseId");

ALTER TABLE "study_materials" ADD CONSTRAINT "study_materials_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_materials" ADD CONSTRAINT "study_materials_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_materials" ADD CONSTRAINT "study_materials_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "study_courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "study_chunks" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "materialId" TEXT NOT NULL,
    "chunkIndex" INTEGER NOT NULL DEFAULT 0,
    "content" TEXT NOT NULL,
    "embedding" DOUBLE PRECISION[] DEFAULT ARRAY[]::DOUBLE PRECISION[],
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "study_chunks_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "study_chunks_tenantId_userId_courseId_idx" ON "study_chunks"("tenantId", "userId", "courseId");
CREATE INDEX "study_chunks_materialId_idx" ON "study_chunks"("materialId");

ALTER TABLE "study_chunks" ADD CONSTRAINT "study_chunks_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "study_courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_chunks" ADD CONSTRAINT "study_chunks_materialId_fkey"
    FOREIGN KEY ("materialId") REFERENCES "study_materials"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "study_cards" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "materialId" TEXT NOT NULL DEFAULT '',
    "front" TEXT NOT NULL,
    "back" TEXT NOT NULL,
    "tags" TEXT NOT NULL DEFAULT '',
    "reps" INTEGER NOT NULL DEFAULT 0,
    "intervalDays" INTEGER NOT NULL DEFAULT 0,
    "ease" DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    "dueAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastReviewedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "study_cards_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "study_cards_tenantId_userId_courseId_dueAt_idx" ON "study_cards"("tenantId", "userId", "courseId", "dueAt");

ALTER TABLE "study_cards" ADD CONSTRAINT "study_cards_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_cards" ADD CONSTRAINT "study_cards_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "study_cards" ADD CONSTRAINT "study_cards_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "study_courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- 3. 企微 / QQ 机器人绑定与主动推送偏好
CREATE TABLE "im_bindings" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "platform" TEXT NOT NULL,
    "externalId" TEXT NOT NULL,
    "displayName" TEXT NOT NULL DEFAULT '',
    "morningDigest" BOOLEAN NOT NULL DEFAULT true,
    "digestTime" TEXT NOT NULL DEFAULT '07:30',
    "gradeAlert" BOOLEAN NOT NULL DEFAULT true,
    "ticketAlert" BOOLEAN NOT NULL DEFAULT true,
    "newsKeywords" TEXT NOT NULL DEFAULT '',
    "lastGradeHash" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "im_bindings_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "im_bindings_tenantId_platform_externalId_key" ON "im_bindings"("tenantId", "platform", "externalId");
CREATE INDEX "im_bindings_tenantId_userId_idx" ON "im_bindings"("tenantId", "userId");

ALTER TABLE "im_bindings" ADD CONSTRAINT "im_bindings_tenantId_fkey"
    FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "im_bindings" ADD CONSTRAINT "im_bindings_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
