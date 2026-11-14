import { progression } from "../../strings/progression";

// The fixed rung order (the register's difficulty vocabulary). Rungs
// always render in this order; methods inside a rung keep the generated
// table's id order. A method without a rung lands in the honest extra
// bucket, rendered last.
export interface Rung {
  key: string;
  label: string;
}

export const RUNGS: readonly Rung[] = [
  { key: "novice", label: progression.progressionRungNovice },
  { key: "intermediate", label: progression.progressionRungIntermediate },
  { key: "advanced", label: progression.progressionRungAdvanced },
];

/** The rung's display label, or the untagged label when difficulty is null. */
export function rungLabel(key: string | null): string {
  if (key === null) {
    return progression.progressionRungUntagged;
  }
  const found = RUNGS.find((rung) => rung.key === key);
  return found === undefined ? progression.progressionRungUntagged : found.label;
}
