import { Button, Card } from "antd";
import { useState } from "react";
import { strings } from "../strings";
import DemoLoadCard from "../components/DemoLoadCard";
import DirectoryCard from "../components/DirectoryCard";
import ResearchIngestCard from "../components/ResearchIngestCard";
import PasteCard from "./ingest/PasteCard";
import TranscriptCard from "./ingest/TranscriptCard";
import UploadCard from "./ingest/UploadCard";
import WebsiteCard from "./ingest/WebsiteCard";

type Tab = "files" | "website" | "paste" | "server" | "transcripts" | "sample";

const TABS: { key: Tab; label: string }[] = [
  { key: "files", label: strings.ingestTabFiles },
  { key: "website", label: strings.ingestTabWebsite },
  { key: "paste", label: strings.ingestTabPaste },
  { key: "server", label: strings.ingestTabServerPath },
  { key: "transcripts", label: strings.ingestTabTranscripts },
  { key: "sample", label: strings.ingestTabSample },
];

/**
 * The Ingest stage — one tab per source, each through the ONE pipeline:
 * files (upload), website (crawl toggle), paste text, server folder,
 * transcripts, and the bundled sample knowledge. The switcher follows
 * the Ask precedent: pressed buttons, no tabs machinery.
 */
export default function Ingest() {
  const [tab, setTab] = useState<Tab>("files");
  return (
    <div className="ui-stack">
      <Card title={strings.ingestTitle}>
        <p>{strings.ingestBody}</p>
      </Card>
      <div className="ui-actions">
        {TABS.map((entry) => (
          <Button
            key={entry.key}
            aria-pressed={tab === entry.key}
            type={tab === entry.key ? "primary" : "default"}
            onClick={() => setTab(entry.key)}
          >
            {entry.label}
          </Button>
        ))}
      </div>
      {tab === "files" && <UploadCard />}
      {tab === "website" && <WebsiteCard />}
      {tab === "paste" && <PasteCard />}
      {tab === "server" && (
        <div className="ui-stack">
          <DirectoryCard title={strings.ingestTabServerPath} />
          {/* Spec F6 puts research/ingest on this tab too (the Scale
              card is the same component over the same op). */}
          <ResearchIngestCard />
        </div>
      )}
      {tab === "transcripts" && <TranscriptCard />}
      {tab === "sample" && <DemoLoadCard />}
    </div>
  );
}
