import { Card } from "antd";
import { strings } from "../strings/setup";
import DemoLoadCard from "../components/DemoLoadCard";
import StatsStrip from "../components/StatsStrip";
import DoctorCard from "./setup/DoctorCard";

/**
 * The Setup stage — the demo walk in order: system status, then the
 * sample-knowledge quick start. The ingest surfaces live on the Ingest
 * stage; this page never duplicates them. The stats strip rides the
 * health area: the two corpus stats ops as badge rows.
 */
export default function Setup() {
  return (
    <div className="ui-stack">
      <Card title={strings.setupTitle}>
        <p>{strings.setupBody}</p>
      </Card>
      <DoctorCard />
      <StatsStrip />
      <DemoLoadCard />
    </div>
  );
}
