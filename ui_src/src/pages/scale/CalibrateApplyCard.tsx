import { Button, Card, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useRef, useState } from "react";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import ConfirmGate from "../../components/ConfirmGate";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

/** One diff row of the lock's proposed overrides (calibrate_tools._diff). */
interface ChangeRow {
  key: string;
  current: unknown;
  proposed: unknown;
  changed: boolean;
}

interface ApplyEnvelope {
  profile: string;
  mode: string;
  config_path: string;
  applied_count: number;
  chosen?: unknown;
  changes: ChangeRow[];
}

interface ApplyBody {
  status: string;
  result: ApplyEnvelope;
}

function isApplyBody(value: unknown): value is ApplyBody {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const result = v.result;
  if (typeof result !== "object" || result === null) return false;
  const r = result as Record<string, unknown>;
  return (
    typeof r.profile === "string" &&
    typeof r.mode === "string" &&
    typeof r.config_path === "string" &&
    typeof r.applied_count === "number" &&
    Number.isFinite(r.applied_count) &&
    Array.isArray(r.changes) &&
    r.changes.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const c = row as Record<string, unknown>;
      return typeof c.key === "string" && typeof c.changed === "boolean";
    })
  );
}

/** One diff cell: strings read as-is; anything else is compact JSON. */
function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

const CHANGE_COLUMNS: ColumnsType<ChangeRow> = [
  { title: strings.scaleCalibrateColumnKey, dataIndex: "key", key: "key" },
  {
    title: strings.scaleCalibrateColumnCurrent,
    key: "current",
    render: (_value: unknown, row: ChangeRow) => valueText(row.current),
  },
  {
    title: strings.scaleCalibrateColumnProposed,
    key: "proposed",
    render: (_value: unknown, row: ChangeRow) => valueText(row.proposed),
  },
];

/**
 * The calibration apply (POST /api/calibrate/apply, issue #200): copy a
 * profile lock's proposed overrides into config.yaml. The default run
 * previews the diff and writes nothing; the write is a separate gated
 * run, so a mutation never happens on a first click.
 */
export default function CalibrateApplyCard() {
  const apply = useOp("calibrate-apply");
  const [profile, setProfile] = useState("");
  const [error, setError] = useState("");
  const [written, setWritten] = useState<number | null>(null);
  const { rootRef, flag } = useFirstErrorFocus();
  const bodyRef = useRef<Record<string, unknown>>({});

  async function run(write: boolean): Promise<void> {
    if (profile.trim() === "") {
      setError(strings.scaleCalibrateProfileRequired);
      flag();
      return;
    }
    setError("");
    bodyRef.current = write ? { profile: profile.trim(), write: true } : { profile: profile.trim() };
    const res = await apply.run(bodyRef.current);
    if (res.ok && write) {
      const env = isApplyBody(res.data) ? res.data.result : null;
      setWritten(env === null ? 0 : env.applied_count);
    }
  }

  function preview(): void {
    setWritten(null);
    void run(false);
  }

  const result = apply.result;
  const env = result !== null && isApplyBody(result.data) ? result.data.result : null;
  const changed = env === null ? 0 : env.changes.filter((row) => row.changed).length;
  const fileName = env === null ? "" : env.config_path.split(/[\\/]/).pop() ?? "";

  let body: ReactNode = null;
  if (result !== null && result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={preview} />;
  } else if (result !== null && !result.ok && env === null) {
    body = <ErrorPanel result={result} onRetry={preview} opId="calibrate-apply" />;
  } else if (env !== null) {
    const chosen = typeof env.chosen === "string" && env.chosen !== "" ? env.chosen : "";
    body = (
      <div className="ui-stack">
        {chosen !== "" && (
          <StatusBanner kind="info">
            {strings.scaleCalibrateChosen.replace("{chosen}", chosen)}
          </StatusBanner>
        )}
        <Table<ChangeRow>
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={env.changes}
          columns={CHANGE_COLUMNS}
        />
        {changed === 0 && <StatusBanner kind="info">{strings.scaleCalibrateNoChanges}</StatusBanner>}
        {changed > 0 && env.mode !== "write" && (
          <div className="ui-actions">
            <ConfirmGate
              title={strings.scaleCalibrateApplyTitle}
              description={strings.scaleCalibrateApplyBody
                .replace("{count}", String(changed))
                .replace("{file}", fileName)}
              confirmLabel={strings.scaleCalibrateApply}
              onConfirm={() => void run(true)}
            />
          </div>
        )}
      </div>
    );
  } else if (result !== null) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  }

  return (
    <Card title={strings.scaleCalibrateTitle}>
      <p className="ui-flush">{strings.scaleCalibrateBody}</p>
      <div className="ui-actions" ref={rootRef}>
        <LabeledField
          label={strings.scaleCalibrateProfileLabel}
          value={profile}
          onChange={setProfile}
          error={error}
          required
          machine
          name="profile"
          widthClass="ui-narrow--form"
          onPressEnter={preview}
        />
        <Button type="primary" loading={apply.running} onClick={preview}>
          {strings.scaleCalibratePreview}
        </Button>
      </div>
      {written !== null && (
        <StatusBanner kind="ok">
          {strings.scaleCalibrateWritten.replace("{count}", String(written))}
        </StatusBanner>
      )}
      {body}
    </Card>
  );
}
