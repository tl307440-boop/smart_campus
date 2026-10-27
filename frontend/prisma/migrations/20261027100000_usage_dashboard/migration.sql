-- CreateTable
CREATE TABLE "message_feedbacks" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "messageId" TEXT NOT NULL,
    "rating" INTEGER NOT NULL,
    "reason" TEXT NOT NULL DEFAULT '',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "message_feedbacks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_logs" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "question" TEXT NOT NULL,
    "outcome" TEXT NOT NULL,
    "tools" TEXT NOT NULL DEFAULT '',
    "guest" BOOLEAN NOT NULL DEFAULT false,
    "messageId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "message_feedbacks_tenantId_rating_createdAt_idx" ON "message_feedbacks"("tenantId", "rating", "createdAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "message_feedbacks_messageId_userId_key" ON "message_feedbacks"("messageId", "userId");

-- CreateIndex
CREATE INDEX "question_logs_tenantId_createdAt_idx" ON "question_logs"("tenantId", "createdAt" DESC);

-- AddForeignKey
ALTER TABLE "message_feedbacks" ADD CONSTRAINT "message_feedbacks_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "message_feedbacks" ADD CONSTRAINT "message_feedbacks_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "message_feedbacks" ADD CONSTRAINT "message_feedbacks_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "chat_messages"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_logs" ADD CONSTRAINT "question_logs_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
