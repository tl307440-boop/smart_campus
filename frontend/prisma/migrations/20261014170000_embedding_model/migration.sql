-- AlterTable
ALTER TABLE "tenant_ai_configs" ADD COLUMN "embeddingProvider" TEXT NOT NULL DEFAULT '',
ADD COLUMN "embeddingModel" TEXT NOT NULL DEFAULT '',
ADD COLUMN "embeddingBaseUrl" TEXT NOT NULL DEFAULT '',
ADD COLUMN "embeddingKey" TEXT NOT NULL DEFAULT '',
ADD COLUMN "embeddingKeyHint" TEXT NOT NULL DEFAULT '';
