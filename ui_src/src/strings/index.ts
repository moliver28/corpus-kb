import en from "./en.json";
import type { AnalyzeStrings } from "./analyze";
import type { AppStrings } from "./app";
import type { AskStrings } from "./ask";
import type { CodebookStrings } from "./codebook";
import type { IngestStrings } from "./ingest";
import type { ReviewStrings } from "./review";

// All user-facing strings live in en.json (zero-drift amendment 2: the
// STE gate reads that file). The domain interfaces pin its shape at
// compile time; a missing key fails poe ui-lint. index.ts stays the
// one import surface (import { strings } from ".../strings").
export interface Strings
  extends AppStrings,
    AskStrings,
    AnalyzeStrings,
    CodebookStrings,
    IngestStrings,
    ReviewStrings {}

export const strings: Strings = en;
