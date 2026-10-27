import { Button } from "antd";
import { useState } from "react";
import PathCard from "../../components/PathCard";
import StatusBanner from "../../components/StatusBanner";
import { strings } from "../../strings";

/**
 * The research ingest (spec F6, bulk start): one file, folder, or glob
 * with ledger dedup — and the force toggle behind its honest warning.
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
