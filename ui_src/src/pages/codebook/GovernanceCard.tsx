import { Button, Card, Segmented, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import ReceiptCard from "../../components/ReceiptCard";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

interface TrafficLight {
  level: unknown;
  message: unknown;
}

interface NoviceView {
  traffic_lights: Record<string, TrafficLight>;
  next_actions: unknown;
}

function isNoviceView(value: unknown): value is NoviceView {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.traffic_lights === "object" && v.traffic_lights !== null;
}

function lightKind(level: unknown): "ok" | "warn" | "error" {
  if (level === "green") return "ok";
  if (level === "red") return "error";
  return "warn";
}

/**
 * The governance report: one schema-pinned artifact per level. The
 * novice view's traffic lights and next actions render as banners when
 * present; the raw artifact stays one click away (ReceiptCard). The
 * level is a selection, the run is the action — and a report from the
 * other level says so instead of posing as current.
 */
export default function GovernanceCard() {
  const [expert, setExpert] = useState(false);
  const [ranLevel, setRanLevel] = useState<"novice" | "expert" | null>(null);
  const report = useOp("research-report");

  async function run(): Promise<void> {
    report.reset();
    setRanLevel(expert ? "expert" : "novice");
    await report.run({ level: expert ? "expert" : "novice" });
  }

  // The shown report is stale when the level moved after the run.
  const currentLevel = expert ? "expert" : "novice";
  const stale = ranLevel !== null && ranLevel !== currentLevel;
  const staleLevel = expert ? strings.codebookLevelExpert : strings.codebookLevelNovice;

  let body: ReactNode = null;
  const result = report.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && result.kind !== "network") {
      const data =
        typeof result.data === "object" && result.data !== null
          ? (result.data as Record<string, unknown>)
          : {};
      const lights: [string, TrafficLight][] = isNoviceView(data.novice_view)
        ? Object.entries(data.novice_view.traffic_lights).filter(
            ([, light]) => typeof light.message === "string",
          )
        : [];
      const actions = isNoviceView(data.novice_view)
        ? data.novice_view.next_actions
        : [];
      body = (
        <div className="ui-stack">
          {stale && (
            <StatusBanner kind="warn">
              {strings.codebookGovernanceStale.replace("{level}", staleLevel)}
            </StatusBanner>
          )}
          {lights.map(([name, light]) => (
            <StatusBanner key={name} kind={lightKind(light.level)}>
              <strong>{name}: </strong>
              {String(light.message)}
            </StatusBanner>
          ))}
          {Array.isArray(actions) && actions.length > 0 && (
            <>
              <Typography.Text strong>{strings.codebookGovernanceActions}</Typography.Text>
              <ul className="ui-citations">
                {actions
                  .filter((action): action is string => typeof action === "string")
                  .map((action) => (
                    <li key={action}>{action}</li>
                  ))}
              </ul>
            </>
          )}
          <ReceiptCard result={result} />
        </div>
      );
    } else {
      body = <ErrorPanel result={result} onRetry={() => void run()} opId="research-report" />;
    }
  }

  return (
    <Card title={strings.codebookGovernanceTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookGovernanceBody}</Typography.Paragraph>
      <div className="ui-actions">
        {/* The level is a segmented selection; the Run button is the one
            action, so selection and action never share a look. */}
        <Segmented
          aria-label={strings.codebookGovernanceTitle}
          options={[
            { label: strings.codebookLevelNovice, value: "novice" },
            { label: strings.codebookLevelExpert, value: "expert" },
          ]}
          value={currentLevel}
          onChange={(value) => setExpert(value === "expert")}
        />
        <Button type="primary" loading={report.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}
