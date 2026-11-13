// The ingest stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./ingest.json";
import { strings as appTable, type AppStrings } from "./app";

export interface IngestStrings {
  ingestBody: string;
  ingestCrawlToggle: string;
  ingestPasteNotWired: string;
  ingestTabFiles: string;
  ingestTabPaste: string;
  ingestTabQueue: string;
  ingestTabSample: string;
  ingestTabServerPath: string;
  ingestTabTranscripts: string;
  ingestTabWebsite: string;
  ingestTitle: string;
  ingestTranscriptPathLabel: string;
  ingestTranscriptPathRequired: string;
  ingestUrlLabel: string;
  ingestUrlRequired: string;
  queueAttemptsLine: string;
  queueBackoff: string;
  queueCancel: string;
  queueCancelConfirmBody: string;
  queueCancelConfirmDismiss: string;
  queueCancelConfirmOk: string;
  queueCancelConfirmTitle: string;
  queueCancelDone: string;
  queueChunksLine: string;
  queueColumnActions: string;
  queueColumnAttempts: string;
  queueColumnElapsed: string;
  queueColumnError: string;
  queueColumnFile: string;
  queueColumnProgress: string;
  queueColumnStatus: string;
  queueConfigInvalid: string;
  queueConfigLabel: string;
  queueDeduplicated: string;
  queueElapsedLine: string;
  queueEmpty: string;
  queueEnqueueAction: string;
  queueEnqueued: string;
  queueFormBody: string;
  queueFormTitle: string;
  queuePagesLine: string;
  queuePathLabel: string;
  queuePathRequired: string;
  queuePaused: string;
  queuePolling: string;
  queueProfileLabel: string;
  queueStatusCancelled: string;
  queueStatusDone: string;
  queueStatusFailed: string;
  queueStatusPending: string;
  queueStatusRunning: string;
  queueTableTitle: string;
  queueTitleLabel: string;
  setupIngestUrlBody: string;
  setupIngestUrlTitle: string;
  setupUploadAction: string;
  setupUploadBody: string;
  setupUploadCap: string;
  setupUploadPick: string;
  setupUploadPickFirst: string;
  setupUploadTitle: string;
  setupUploadTooLarge: string;
}

export const strings: AppStrings & IngestStrings = {
  ...appTable,
  ...tableJson,
};
