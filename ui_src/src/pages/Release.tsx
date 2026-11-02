import { Card, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import { strings } from "../strings";
import IdFormCard from "../components/IdFormCard";
import type { FormField } from "../components/IdFormCard";
import type { OpResult } from "../api/types";
import ReceiptCard from "../components/ReceiptCard";
import StatusBanner from "../components/StatusBanner";

const VERSION: FormField = {
  key: "codebook_version_id",
  label: strings.releaseVersionLabel,
  required: true,
};
const RELEASE: FormField = { key: "release_id", label: strings.releaseReleaseLabel, required: true };

/** The assess receipt: the gate table plus the required-fail line. */
function renderAssess(result: OpResult): ReactNode {
  const data =
    typeof result.data === "object" && result.data !== null
      ? (result.data as Record<string, unknown>)
      : {};
  const rows = Array.isArray(data.gate_results) ? data.gate_results : [];
  const failing = Array.isArray(data.required_failing) ? data.required_failing : [];
  const allPass = data.all_required_pass_without_waivers === true;
  return (
    <div className="ui-stack">
      <Typography.Title level={5}>{strings.releaseGatesHeading}</Typography.Title>
      {rows.map((row, index) => {
        const gate = typeof row === "object" && row !== null ? (row as Record<string, unknown>) : {};
        const pass = gate.status === "pass";
        return (
          <StatusBanner key={index} kind={pass ? "ok" : "warn"}>
            {String(gate.gate_id ?? "")}: {String(gate.reason ?? gate.status ?? "")}
          </StatusBanner>
        );
      })}
      {allPass ? (
        <StatusBanner kind="ok">{strings.releaseAllPass}</StatusBanner>
      ) : (
        <StatusBanner kind="warn">
          {strings.releaseRequiredFailing} {failing.join(", ")}
        </StatusBanner>
      )}
    </div>
  );
}

/** The review packet receipt: the recommended next action up front. */
function renderReview(result: OpResult): ReactNode {
  const data =
    typeof result.data === "object" && result.data !== null
      ? (result.data as Record<string, unknown>)
      : {};
  const next = typeof data.recommended_next_action === "string" ? data.recommended_next_action : "";
  const unmet = Array.isArray(data.unmet_required_gates) && data.unmet_required_gates.length > 0;
  return (
    <div className="ui-stack">
      {next !== "" && <StatusBanner kind={unmet ? "warn" : "info"}>{next}</StatusBanner>}
      <ReceiptCard result={result} />
    </div>
  );
}

/** The gate-state lines the execute dialog shows (from the last assess). */
function gateSummaryOf(result: OpResult): ReactNode {
  const data =
    typeof result.data === "object" && result.data !== null
      ? (result.data as Record<string, unknown>)
      : {};
  const rows = Array.isArray(data.gate_results) ? data.gate_results : [];
  if (rows.length === 0) {
    return null;
  }
  return (
    <>
      <Typography.Text strong>{strings.releaseGatesHeading}</Typography.Text>
      {rows.map((row, index) => {
        const gate = typeof row === "object" && row !== null ? (row as Record<string, unknown>) : {};
        return (
          <p key={index} className="ui-flush">
            <strong>{String(gate.gate_id ?? "")}: </strong>
            {String(gate.status ?? "")} — {String(gate.reason ?? "")}
          </p>
        );
      })}
    </>
  );
}

/**
 * The Release stage — assess, request, review, status, compare, and
 * the gated execute. A blocked run renders as the structured refusal
 * (reason + unmet required gates), never a generic error panel. The
 * execute dialog carries the last assess gate summary.
 */
export default function Release() {
  const [gateSummary, setGateSummary] = useState<ReactNode>(null);
  return (
    <div className="ui-stack">
      <Card title={strings.releaseTitle}>
        <p>{strings.releaseBody}</p>
      </Card>
      <IdFormCard
        title={strings.releaseAssessTitle}
        body={strings.releaseAssessBody}
        fields={[VERSION]}
        opId="release-assess"
        renderResult={renderAssess}
        onResult={(res) => setGateSummary(gateSummaryOf(res))}
      />
      <IdFormCard
        title={strings.releaseRequestTitle}
        fields={[
          VERSION,
          { key: "creator", label: strings.releaseRequestCreatorLabel, required: true },
        ]}
        opId="release-request"
      />
      <IdFormCard
        title={strings.releaseReviewTitle}
        fields={[VERSION, RELEASE]}
        opId="release-review"
        renderResult={renderReview}
      />
      <IdFormCard
        title={strings.releaseStatusTitle}
        fields={[VERSION]}
        opId="release-status"
        via="query"
        renderResult={renderReview}
      />
      <IdFormCard
        title={strings.releaseExecuteTitle}
        fields={[
          VERSION,
          RELEASE,
          { key: "approver", label: strings.releaseExecuteApproverLabel, required: true },
          { key: "waivers", label: strings.releaseWaiversLabel, json: true },
        ]}
        opId="release-execute"
        gate
        gateSummary={gateSummary}
      />
      <IdFormCard
        title={strings.releaseCompareTitle}
        fields={[
          VERSION,
          { key: "old_release_id", label: strings.releaseOldLabel, required: true },
          { key: "new_release_id", label: strings.releaseNewLabel, required: true },
        ]}
        opId="release-compare"
      />
    </div>
  );
}
