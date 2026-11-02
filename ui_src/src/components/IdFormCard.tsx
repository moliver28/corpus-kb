import { Button, Card, Input, Modal, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";
import BlockedPanel from "./BlockedPanel";
import JsonViewer from "./JsonViewer";
import ErrorPanel from "./ErrorPanel";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { strings } from "../strings";

export interface FormField {
  key: string;
  label: string;
  required?: boolean;
  /** Parse the value as JSON before sending (waiver pairs). */
  json?: boolean;
}

/**
 * One typed release form: named id fields over ONE manifest op. `via`
 * picks the transport shape — a JSON body or a query-string GET.
 * `gate` wraps the run in a confirm dialog that shows `gateSummary`
 * (the assess/review gate state) above every field value, so the
 * approver sees the gate state before confirming (spec F9).
 */
export default function IdFormCard({
  title,
  body,
  fields,
  opId,
  via = "body",
  gate = false,
  gateSummary,
  renderResult,
  onResult,
}: {
  title: string;
  body?: string;
  fields: FormField[];
  opId: string;
  via?: "body" | "query";
  gate?: boolean;
  gateSummary?: ReactNode;
  renderResult?: (result: OpResult) => ReactNode;
  onResult?: (result: OpResult) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [hint, setHint] = useState("");
  const [gateOpen, setGateOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<OpResult | null>(null);
  const op = getOp(opId);

  function setValue(key: string, value: string): void {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function buildBody(): { ok: boolean; body: Record<string, unknown> } {
    const out: Record<string, unknown> = {};
    for (const field of fields) {
      const value = (values[field.key] ?? "").trim();
      if (value === "") continue;
      if (field.json === true) {
        try {
          out[field.key] = JSON.parse(value);
        } catch {
          return { ok: false, body: {} };
        }
      } else {
        out[field.key] = value;
      }
    }
    return { ok: true, body: out };
  }

  async function run(): Promise<void> {
    if (op === undefined || running) {
      return;
    }
    if (fields.some((field) => field.required && (values[field.key] ?? "").trim() === "")) {
      setHint(strings.formRequired);
      return;
    }
    const built = buildBody();
    if (!built.ok) {
      setHint(strings.runnerInvalidJson);
      return;
    }
    setHint("");
    setRunning(true);
    const payload = built.body;
    const res =
      via === "query"
        ? await callOp<unknown>(
            `${op.path}?${new URLSearchParams(payload as Record<string, string>)}`,
            { method: "GET" },
          )
        : await callOp<unknown>(op.path, { method: "POST", body: payload });
    setRunning(false);
    setResult(res);
    onResult?.(res);
  }

  function trigger(): void {
    if (gate) {
      setGateOpen(true);
      return;
    }
    void run();
  }

  let resultView: ReactNode = null;
  if (result !== null && !running) {
    if (result.kind === "unavailable") {
      resultView = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.kind === "blocked" || result.status === "blocked") {
      // A blocked refusal carries its own remediation (the gates); a
      // retry button would just re-submit the same failing release.
      resultView = <BlockedPanel result={result} />;
    } else if (result.ok && result.kind !== "network") {
      resultView = renderResult !== undefined ? renderResult(result) : <ReceiptCard result={result} />;
    } else if (result.kind === "unknown") {
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else {
      resultView = <ErrorPanel result={result} onRetry={() => void run()} opId={opId} />;
    }
  }

  return (
    <Card title={title}>
      {body !== undefined && (
        <Typography.Paragraph type="secondary">{body}</Typography.Paragraph>
      )}
      <div className="ui-stack">
        {fields.map((field) => (
          <div key={field.key}>
            <Typography.Text>
              {field.label}
              {field.required === true ? " *" : ""}:{" "}
            </Typography.Text>
            <Input
              className="ui-narrow--form"
              value={values[field.key] ?? ""}
              onChange={(event) => setValue(field.key, event.target.value)}
              placeholder={field.label}
            />
          </div>
        ))}
        <div className="ui-actions">
          <Button type="primary" loading={running} onClick={trigger}>
            {strings.runnerRunLabel}
          </Button>
        </div>
        {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
        {running && <Skeleton active title={false} paragraph={{ rows: 2 }} />}
        {resultView}
      </div>
      <Modal
        title={strings.releaseExecuteConfirmTitle}
        open={gateOpen}
        onOk={() => {
          setGateOpen(false);
          void run();
        }}
        onCancel={() => setGateOpen(false)}
        okText={strings.confirmConfirm}
        cancelText={strings.confirmCancel}
      >
        {gateSummary !== null && gateSummary !== undefined && (
          <div className="ui-stack">{gateSummary}</div>
        )}
        {fields.map((field) => (
          <p key={field.key}>
            <strong>{field.label}: </strong>
            {(values[field.key] ?? "").trim() === "" ? "-" : values[field.key]}
          </p>
        ))}
      </Modal>
    </Card>
  );
}
