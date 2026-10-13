-- CreateTable
CREATE TABLE "tenant_ai_configs" (
    "tenantId" TEXT NOT NULL,
    "chatBaseUrl" TEXT NOT NULL DEFAULT '',
    "chatModel" TEXT NOT NULL DEFAULT '',
    "chatKey" TEXT NOT NULL DEFAULT '',
    "chatKeyHint" TEXT NOT NULL DEFAULT '',
    "readerBaseUrl" TEXT NOT NULL DEFAULT '',
    "readerModel" TEXT NOT NULL DEFAULT '',
    "readerKey" TEXT NOT NULL DEFAULT '',
    "readerKeyHint" TEXT NOT NULL DEFAULT '',
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "tenant_ai_configs_pkey" PRIMARY KEY ("tenantId")
);

-- AddForeignKey
ALTER TABLE "tenant_ai_configs" ADD CONSTRAINT "tenant_ai_configs_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;
