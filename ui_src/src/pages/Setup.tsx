import { Card } from "antd";
import { strings } from "../strings";
import DoctorCard from "./setup/DoctorCard";
import DemoLoadCard from "./setup/DemoLoadCard";
import IngestRunnerCard from "./setup/IngestRunnerCard";
import UploadCard from "./setup/UploadCard";

/**
 * The Setup stage — the demo walk in order: system status, sample
 * knowledge, own files, then server-folder and web ingest.
 */
export default function Setup() {
  return (
    <div className="ui-stack">
      <Card title={strings.setupTitle}>
        <p>{strings.setupBody}</p>
      </Card>
      <DoctorCard />
      <DemoLoadCard />
      <UploadCard />
      <IngestRunnerCard />
    </div>
  );
}
