import { Button, Card, Skeleton, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { allChecks, envelopeResult, isDoctorResult, stateWord } from "./doctor";
import type { CheckRow, DoctorResult } from "./doctor";

/** The doctor table itself: reachability banners plus the check rows. */
function DoctorView({ result }: { result: DoctorResult }) {
  const rows: CheckRow[] = allChecks(result);
  const columns: ColumnsType<CheckRow> = [
    { title: strings.setupColumnCheck, dataIndex: ["check", "name"] },
    {
      title: strings.setupColumnState,
      dataIndex: ["check", "status"],
      render: (value: unknown) => <Tag>{stateWord(String(value))}</Tag>,
    },
    { title: strings.setupColumnDetail, dataIndex: ["check", "detail"] },
    { title: strings.setupColumnFix, dataIndex: ["check", "fix"] },
  ];
  return (
    <div className="ui-stack">
      <StatusBanner kind={result.postgres.reachable ? "ok" : "warn"}>
        {result.postgres.reachable
          ? strings.setupDoctorDbOk
          : strings.setupDoctorDbDown}{" "}
        {result.postgres.message}
      </StatusBanner>
      <StatusBanner kind={result.ollama.reachable ? "ok" : "warn"}>
        {result.ollama.reachable
          ? strings.setupDoctorOllamaOk
          : strings.setupDoctorOllamaDown}{" "}
        {result.ollama.message}
      </StatusBanner>
      <Typography.Paragraph type="secondary">
        {strings.setupDoctorProfile
          .replace("{profile}", result.profile.profile)
          .replace("{model}", result.profile.recommended_model)}
      </Typography.Paragraph>
      <Typography.Title level={5}>{strings.setupDoctorChecksHeading}</Typography.Title>
      {rows.length === 0 ? (
        <StatusBanner kind="info">{strings.setupDoctorChecksNone}</StatusBanner>
      ) : (
        <Table<CheckRow>
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={rows}
          columns={columns}
        />
      )}
    </div>
  );
}

/**
 * The server status card: one doctor call through useOp, honest states
 * for every outcome. The first call starts on mount; Check again reruns.
 */
export default function DoctorCard() {
  const { run, result, running, reset } = useOp("doctor");

  // useOp's run is not referentially stable, so the mount call goes
  // through a ref: one GET on mount, never a re-fetch loop.
  const runRef = useRef<() => Promise<unknown>>(run);
  useEffect(() => {
    void runRef.current();
  }, []);

  function refresh(): void {
    reset();
    void run();
  }

  let body: ReactNode;
  if (result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 4 }} />;
  } else if (result.kind === "network") {
    body = <ErrorPanel result={result} onRetry={refresh} />;
  } else if (result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={refresh} />;
  } else {
    // The doctor envelope is {status, result}; the screen renders the
    // inner object and keeps the raw envelope one click away.
    const inner = envelopeResult(result.data);
    body = isDoctorResult(inner) ? (
      <DoctorView result={inner} />
    ) : (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  }

  return (
    <Card
      title={strings.setupDoctorTitle}
      extra={
        <Button onClick={refresh} loading={running}>
          {strings.setupDoctorRefresh}
        </Button>
      }
    >
      {body}
    </Card>
  );
}
