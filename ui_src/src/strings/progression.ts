import raw from "./progression.json";

// The progression rail's string table (novice-UX W3-ui). It stays out of
// strings/index.ts on purpose: index.ts rides the entry chunk, the rail
// loads in its own lazy chunk, and the rail's labels must not grow the
// initial route bundle. The gate is tests/test_ui_progression_strings_ste.py.
export interface ProgressionStrings {
  progressionTitle: string;
  progressionBody: string;
  progressionRungNovice: string;
  progressionRungIntermediate: string;
  progressionRungAdvanced: string;
  progressionRungUntagged: string;
  progressionMethodCount: string;
  progressionMethodCountOne: string;
  progressionTeachesLabel: string;
  progressionNeedsLabel: string;
  progressionNextLabel: string;
  progressionHumanOnly: string;
  progressionRungEmpty: string;
}

export const progression: ProgressionStrings = raw;
