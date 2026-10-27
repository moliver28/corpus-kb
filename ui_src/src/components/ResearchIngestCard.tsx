import { Button } from "antd";
import { useState } from "react";
import PathCard from "./PathCard";
import StatusBanner from "./StatusBanner";
import { strings } from "../strings";

/**
 * The research ingest (spec F6, bulk start): one file, folder, or glob
 * with ledger dedup. The force toggle rides its honest warning, and the
 * confirm dialog repeats it (spec F10: force re-ingest is a gated run).
 */
export default function ResearchIngestCard() {
  const [force, setForce] = useState(false);
  return (
    <PathCard
      title={strings.scaleResearchTitle}
      body={strings.scaleResearchBody}
      label={strings.scaleResearchPathLabel}
      requiredMessage={strings.scaleResearchPathRequired}
      pathKey="path"
      extraBody={{ force }}
      opId="research-ingest"
      gateDescription={force ? strings.scaleForceWarning : undefined}
    >
      <div className="ui-actions">
        <Button
          aria-pressed={force}
          type={force ? "primary" : "default"}
          onClick={() => setForce(!force)}
        >
          {strings.scaleForceToggle}
        </Button>
      </div>
      {force && <StatusBanner kind="warn">{strings.scaleForceWarning}</StatusBanner>}
    </PathCard>
  );
}
