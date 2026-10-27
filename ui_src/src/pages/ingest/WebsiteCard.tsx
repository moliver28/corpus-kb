import { Button } from "antd";
import { useState } from "react";
import PathCard from "../../components/PathCard";
import { strings } from "../../strings";

/**
 * The website ingest: one address, with the crawl toggle that walks
 * same-domain pages instead of the single page. One op (ingest-url),
 * one typed form — no second pipeline.
 */
export default function WebsiteCard() {
  const [crawl, setCrawl] = useState(false);
  return (
    <PathCard
      title={strings.setupIngestUrlTitle}
      body={strings.setupIngestUrlBody}
      label={strings.ingestUrlLabel}
      requiredMessage={strings.ingestUrlRequired}
      pathKey="url"
      extraBody={{ crawl }}
      opId="ingest-url"
    >
      <div className="ui-actions">
        <Button
          aria-pressed={crawl}
          type={crawl ? "primary" : "default"}
          onClick={() => setCrawl(!crawl)}
        >
          {strings.ingestCrawlToggle}
        </Button>
      </div>
    </PathCard>
  );
}
