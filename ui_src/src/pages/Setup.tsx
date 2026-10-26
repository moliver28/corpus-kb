import { Card } from "antd";
import { strings } from "../strings";

/** The Setup stage: doctor checks and settings (built in Task 6). */
export default function Setup() {
  return (
    <Card title={strings.setupTitle}>
      <p>{strings.setupBody}</p>
    </Card>
  );
}
