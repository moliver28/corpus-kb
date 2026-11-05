import { Skeleton } from "antd";
import type { ReactNode } from "react";
import type { OpResult } from "../api/types";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { strings } from "../strings";

/**
 * The one honest body for a settled read: load, network, unavailable,
 * drift (warn plus the raw JSON), then the caller's rows. Every card
 * that reads one op dispatches through this component; the kind
 * vocabulary lives in api/errors.ts, the panels stay theirs.
 */
export default function OpBody({
  result,
  ok,
  retry,
  opId,
  skeletonRows = 4,
  children,
}: {
  result: OpResult<unknown> | null;
  /** The envelope guard passed (the payload is the shape we render). */
  ok: boolean;
  retry: () => void;
  opId: string;
  skeletonRows?: number;
  children: ReactNode;
}) {
  if (result === null) {
    return <Skeleton active title={false} paragraph={{ rows: skeletonRows }} />;
  }
  if (result.kind === "network") {
    return <ErrorPanel result={result} onRetry={retry} opId={opId} />;
  }
  if (result.kind === "unavailable") {
    return <UnavailablePanel result={result} onRetry={retry} />;
  }
  if (!ok) {
    return (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  }
  return <>{children}</>;
}
