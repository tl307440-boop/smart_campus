-- AlterTable
ALTER TABLE "tenant_ai_configs" ADD COLUMN     "visionBaseUrl" TEXT NOT NULL DEFAULT '',
ADD COLUMN     "visionKey" TEXT NOT NULL DEFAULT '',
ADD COLUMN     "visionKeyHint" TEXT NOT NULL DEFAULT '',
ADD COLUMN     "visionModel" TEXT NOT NULL DEFAULT '';
