import { Button, Card, Typography } from "antd";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import ReceiptCard from "../../components/ReceiptCard";

import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

/**
 * The governance report: one schema-pinned artifact per level. The
 * report payload carries no status word, so its receipt renders
 * directly (the raw artifact is the point).
 */
export default function GovernanceCard() {
  const [expert, setExpert] = useState(false);
  const report = useOp("research-report");

  async function run(): Promise<void> {
    report.reset();
    await report.run({ level: expert ? "expert" : "novice" });
  }

  let body = null;
  const result = report.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && result.kind !== "network") {
      // A settled report (or an honest error envelope) renders as-is.
      body = <ReceiptCard result={result} />;
    } else {
      body = <ErrorPanel result={result} onRetry={() => void run()} opId="research-report" />;
    }
  }

  return (
    <Card title={strings.codebookGovernanceTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookGovernanceBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button
          aria-pressed={!expert}
          type={expert ? "default" : "primary"}
          onClick={() => setExpert(false)}
        >
          {strings.codebookLevelNovice}
        </Button>
        <Button
          aria-pressed={expert}
          type={expert ? "primary" : "default"}
          onClick={() => setExpert(true)}
        >
          {strings.codebookLevelExpert}
        </Button>
        <Button type="primary" loading={report.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}
