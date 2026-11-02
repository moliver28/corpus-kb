import type { ReactNode } from "react";
import { strings } from "../strings";

type BannerKind = "info" | "ok" | "warn" | "error";

/** The word names the state — color is never the only signal. */
const KIND_WORD: Record<BannerKind, string> = {
  info: strings.bannerKindInfo,
  ok: strings.bannerKindOk,
  warn: strings.bannerKindWarn,
  error: strings.bannerKindError,
};

/** One status line: a kind word plus the message. */
export default function StatusBanner({
  kind,
  children,
}: {
  kind: BannerKind;
  children: ReactNode;
}) {
  return (
    <div className={`ui-banner ui-banner--${kind}`} role="status">
      <span className="ui-banner__kind">{KIND_WORD[kind]}:</span>
      <span>{children}</span>
    </div>
  );
}
