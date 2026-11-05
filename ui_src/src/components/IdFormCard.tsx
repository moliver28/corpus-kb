import { Button, Card, Modal, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import type { OpResult } from "../api/types";
import BlockedPanel from "./BlockedPanel";
import LabeledField, { useFirstErrorFocus } from "./LabeledField";
import ErrorPanel from "./ErrorPanel";
import OpBody from "./OpBody";
import ReceiptCard from "./ReceiptCard";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings";

export interface FormField {
  key: string;
  label: string;
  required?: boolean;
  /** Parse the value as JSON before sending (waiver pairs). */
  json?: boolean;
}

/**
 * One typed release form over ONE manifest op through useOp, the one
 * op hook. `via` picks the transport shape — a JSON body, or query
 * pairs appended to the op path. The manifest's method always rides:
 * a query form sends the op's declared verb with no body. Query pairs
 * carry string fields only: a query pair is a flat string, and a
 * structured value would arrive as text the server cannot read back
 * into the field (no manifest query op declares a structured field).
 * A failed JSON parse names the field.
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
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [gateOpen, setGateOpen] = useState(false);
  const op = useOp(opId);
  const { rootRef, flag } = useFirstErrorFocus();

  function setValue(key: string, value: string): void {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  /** One pass: trim, check required, parse JSON, split body vs query. */
  function build(): {
    errors: Record<string, string>;
    body: Record<string, unknown>;
    query: Record<string, string>;
  } {
    const errors: Record<string, string> = {};
    const body: Record<string, unknown> = {};
    const query: Record<string, string> = {};
    for (const field of fields) {
      const raw = (values[field.key] ?? "").trim();
      if (raw === "") {
        if (field.required === true) {
          errors[field.key] = strings.formFieldRequired.replace("{field}", field.label);
        }
        continue;
      }
      if (field.json === true) {
        // Query pairs carry flat strings, so JSON fields ride a body
        // only. A query form refuses the field by name instead of
        // dropping the text the user typed.
        if (via === "query") {
          errors[field.key] = strings.formFieldBodyOnly.replace("{field}", field.label);
          continue;
        }
        try {
          body[field.key] = JSON.parse(raw) as unknown;
        } catch {
          errors[field.key] = strings.formInvalidJson.replace("{field}", field.label);
        }
        continue;
      }
      if (via === "query") {
        query[field.key] = raw;
      } else {
        body[field.key] = raw;
      }
    }
    return { errors, body, query };
  }

  async function run(): Promise<void> {
    if (op.running) {
      return;
    }
    const built = build();
    if (Object.keys(built.errors).length > 0) {
      setErrors(built.errors);
      flag();
      return;
    }
    setErrors({});
    const res =
      via === "query"
        ? await op.run(undefined, built.query)
        : await op.run(built.body);
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
  if (op.result !== null && !op.running) {
    const result = op.result;
    resultView = (
      <OpBody
        result={result}
        ok={result.ok && result.kind !== "unknown"}
        retry={() => void run()}
        opId={opId}
        failure={(failed) =>
          failed.kind === "blocked" || failed.status === "blocked" ? (
            // A blocked refusal carries its own remediation (the gates);
            // a retry button would just re-submit the same failing
            // release.
            <BlockedPanel result={failed} />
          ) : (
            <ErrorPanel result={failed} onRetry={() => void run()} opId={opId} />
          )
        }
      >
        {renderResult !== undefined ? renderResult(result) : <ReceiptCard result={result} />}
      </OpBody>
    );
  }

  return (
    <Card title={title}>
      {body !== undefined && (
        <Typography.Paragraph type="secondary">{body}</Typography.Paragraph>
      )}
      <div className="ui-stack" ref={rootRef}>
        {fields.map((field) => (
          <LabeledField
            key={field.key}
            label={field.label}
            value={values[field.key] ?? ""}
            onChange={(value) => setValue(field.key, value)}
            error={errors[field.key]}
            required={field.required === true}
            machine
            name={field.key}
            widthClass="ui-narrow--form"
          />
        ))}
        <div className="ui-actions">
          <Button type="primary" loading={op.running} onClick={trigger}>
            {strings.runnerRunLabel}
          </Button>
        </div>
        {op.running && <Skeleton active title={false} paragraph={{ rows: 2 }} />}
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
