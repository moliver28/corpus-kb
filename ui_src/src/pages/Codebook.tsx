import { Card } from "antd";
import { strings } from "../strings";
import ActionCard from "../components/ActionCard";
import NotWiredCard from "../components/NotWiredCard";
import PathCard from "../components/PathCard";
import CycleCard from "./codebook/CycleCard";
import GovernanceCard from "./codebook/GovernanceCard";
import PreFlightCard from "./codebook/PreFlightCard";
import PromoteCard from "./codebook/PromoteCard";
import { EvidenceCard, OverlapCard, UncodedCard } from "./codebook/NotebookCards";

/** The honest not-wired rows for the coding-pipeline steps (op id, line). */
const NOT_WIRED = [
  { opId: "coding-embed-codebook", text: strings.codebookEmbedNotWired },
  { opId: "coding-pool", text: strings.codebookPoolNotWired },
  { opId: "coding-batch", text: strings.codebookBatchNotWired },
  { opId: "coding-calibrate", text: strings.codebookCalibrateNotWired },
  { opId: "coding-reliability", text: strings.codebookReliabilityNotWired },
  { opId: "coding-saturation", text: strings.codebookSaturationNotWired },
];

/**
 * The Codebook stage — pre-flight, the two wired coding steps, the
 * promote gate, the notebook views, the governance report, and the
 * one-stage-per-click cycle wizard. The unwired pipeline steps say so.
 */
export default function Codebook() {
  return (
    <div className="ui-stack">
      <Card title={strings.codebookTitle}>
        <p>{strings.codebookBody}</p>
      </Card>
      <PreFlightCard />
      <Card title={strings.codebookStepsTitle}>
        <div className="ui-stack">
          <ActionCard
            opId="coding-inductive"
            title={strings.codebookInductiveTitle}
            body={strings.codebookInductiveBody}
          />
          <PathCard
            title={strings.codebookRunTitle}
            body={strings.codebookRunBody}
            label={strings.codebookVersionLabel}
            requiredMessage={strings.codebookVersionRequired}
            pathKey="codebook_version_id"
            opId="coding-run"
          />
        </div>
      </Card>
      <PromoteCard />
      <Card title={strings.codebookNotebookTitle}>
        <div className="ui-stack">
          <EvidenceCard />
          <UncodedCard />
          <OverlapCard />
        </div>
      </Card>
      <GovernanceCard />
      <CycleCard />
      <NotWiredCard title={strings.codebookNotWiredTitle} items={NOT_WIRED} />
    </div>
  );
}
