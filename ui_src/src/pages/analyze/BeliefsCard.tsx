import { Card, Tabs, Typography } from "antd";
import { strings } from "../../strings/analyze";
import BeliefsComparePane from "./BeliefsComparePane";
import BeliefsCurrentPane from "./BeliefsCurrentPane";
import BeliefsPredictPane from "./BeliefsPredictPane";
import BeliefsTimelinePane from "./BeliefsTimelinePane";

/**
 * The beliefs family in one card: current posteriors, era history,
 * posterior prediction, and the side-by-side comparison. One family,
 * one card (LabelingCard pattern); each tab is its own pane file.
 */
export default function BeliefsCard() {
  return (
    <Card title={strings.analyzeBeliefsCardTitle}>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.analyzeBeliefsCardBody}
      </Typography.Paragraph>
      <Tabs
        items={[
          {
            key: "current",
            label: strings.analyzeBeliefsTabCurrent,
            children: <BeliefsCurrentPane />,
          },
          {
            key: "timeline",
            label: strings.analyzeBeliefsTabTimeline,
            children: <BeliefsTimelinePane />,
          },
          {
            key: "predict",
            label: strings.analyzeBeliefsTabPredict,
            children: <BeliefsPredictPane />,
          },
          {
            key: "compare",
            label: strings.analyzeBeliefsTabCompare,
            children: <BeliefsComparePane />,
          },
        ]}
      />
    </Card>
  );
}
