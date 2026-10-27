import { Card, Skeleton, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

interface ListEnvelope {
  status: string;
  result: unknown[];
}

function isListEnvelope(value: unknown): value is ListEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.status === "string" && Array.isArray(v.result);
}

/** One doctor research check row (tools/_setup doctor_research.py). */
interface DimCheck {
  name: string;
  status: string;
  detail: string;
}

function isDimCheck(value: unknown): value is DimCheck {
  if (typeof value !== "object" || value === null) return false;
  const c = value as Record<string, unknown>;
  return (
    typeof c.name === "string" && typeof c.status === "string" && typeof c.detail === "string"
  );
}

/**
 * The pre-flight: research projects, codebook versions, and the
 * embedding-dimension rows the doctor already reports. Reads only.
 */
export default function PreFlightCard() {
  const projects = useOp("projects-list");
  const versions = useOp("codebooks-list");
  const doctor = useOp("doctor");

  // One GET each on mount through a ref (useOp's run is not stable).
  const ref = useRef<() => Promise<unknown>>(() => Promise.all([projects.run(), versions.run(), doctor.run()]));
  useEffect(() => {
    void ref.current();
  }, []);

  function refresh(): void {
    projects.reset();
    versions.reset();
    doctor.reset();
    void ref.current();
  }

  function readRows(result: typeof projects.result): { rows: unknown[]; node: ReactNode } {
    if (result === null) {
      return { rows: [], node: <Skeleton active title={false} paragraph={{ rows: 2 }} /> };
    }
    if (result.kind === "network") {
      return { rows: [], node: <ErrorPanel result={result} onRetry={refresh} /> };
    }
    if (result.kind === "unavailable") {
      return { rows: [], node: <UnavailablePanel result={result} onRetry={refresh} /> };
    }
    if (isListEnvelope(result.data)) {
      return { rows: result.data.result, node: null };
    }
    return {
      rows: [],
      node: <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>,
    };
  }

  const projectView = readRows(projects.result);
  const versionView = readRows(versions.result);
  const columns: ColumnsType<Record<string, unknown>> = [
    { title: strings.releaseVersionLabel, dataIndex: "version_id", key: "version_id" },
    { title: strings.codebookColumnLabel, dataIndex: "label", key: "label" },
    { title: "SHA-256", dataIndex: "sha256", key: "sha256" },
  ];

  let dims: ReactNode;
  const doctorResult = doctor.result;
  if (doctorResult === null) {
    dims = <Skeleton active title={false} paragraph={{ rows: 1 }} />;
  } else if (doctorResult.ok && typeof doctorResult.data === "object" && doctorResult.data !== null) {
    const inner = (doctorResult.data as Record<string, unknown>).result;
    const checks =
      typeof inner === "object" && inner !== null && Array.isArray((inner as Record<string, unknown>).research_checks)
        ? ((inner as Record<string, unknown>).research_checks as unknown[])
        : [];
    const dimRows = checks.filter(isDimCheck).filter((check) => /dimension/i.test(`${check.name} ${check.detail}`));
    dims =
      dimRows.length === 0 ? (
        <StatusBanner kind="info">{strings.codebookDimsNone}</StatusBanner>
      ) : (
        dimRows.map((check) => (
          <StatusBanner key={check.name} kind={check.status === "ok" ? "ok" : "warn"}>
            {check.detail}
          </StatusBanner>
        ))
      );
  } else {
    dims = <StatusBanner kind="info">{strings.codebookDimsNone}</StatusBanner>;
  }

  return (
    <Card title={strings.codebookPreFlightTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookPreFlightBody}</Typography.Paragraph>
      <div className="ui-stack">
        <StatusBanner kind="info">
          {strings.codebookProjectsLine.replace("{count}", String(projectView.rows.length))}
        </StatusBanner>
        {projectView.node}
        <Typography.Title level={5}>{strings.codebookVersionsHeading}</Typography.Title>
        {versionView.node}
        {versionView.rows.length === 0 && versionView.node === null && (
          <StatusBanner kind="info">{strings.codebookVersionsNone}</StatusBanner>
        )}
        {versionView.rows.length > 0 && (
          <Table<Record<string, unknown>>
            size="small"
            rowKey="version_id"
            pagination={false}
            dataSource={versionView.rows as Record<string, unknown>[]}
            columns={columns}
          />
        )}
        <Typography.Title level={5}>{strings.codebookDimsHeading}</Typography.Title>
        {dims}
      </div>
    </Card>
  );
}
