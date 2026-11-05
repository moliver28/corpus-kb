import { Skeleton } from "antd";
import type { ReactNode } from "react";
import type { OpResult } from "../api/types";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { strings } from "../strings";

/** The drift block: the honest warn plus the raw JSON, one click deep. */
export function DriftView({ data }: { data: unknown }): ReactNode {
  return (
    <div>
      <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
      <JsonViewer value={data} />
    </div>
  );
}

/**
 * The one honest body for a settled read: load, network, unavailable,
 * failure, drift (warn plus the raw JSON), then the caller's rows. A
 * card whose failures need the full error panel passes `failure`; the
 * default renders the drift block for a failed call. The kind
 * vocabulary lives in api/errors.ts, the panels stay theirs.
 */
export default function OpBody({
  result,
  ok,
  retry,
  opId,
  skeletonRows = 4,
  failure,
  children,
}: {
  result: OpResult<unknown> | null;
  /** The envelope guard passed (the payload is the shape we render). */
  ok: boolean;
  retry: () => void;
  opId: string;
  skeletonRows?: number;
  /** Replaces the default drift block on a failed call (not a drift). */
  failure?: (result: OpResult<unknown>) => ReactNode;
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
  if (!result.ok) {
    return failure !== undefined ? <>{failure(result)}</> : <DriftView data={result.data} />;
  }
  if (!ok) {
    return <DriftView data={result.data} />;
  }
  return <>{children}</>;
}
