import { Button, Card } from "antd";
import { strings } from "../strings/ingest";
import { useQueryParam } from "../hooks/useQueryParam";
import DemoLoadCard from "../components/DemoLoadCard";
import DirectoryCard from "../components/DirectoryCard";
import ResearchIngestCard from "../components/ResearchIngestCard";
import PasteCard from "./ingest/PasteCard";
import QueueCard from "./ingest/QueueCard";
import TranscriptCard from "./ingest/TranscriptCard";
import UploadCard from "./ingest/UploadCard";
import WebsiteCard from "./ingest/WebsiteCard";

const TAB_KEYS = [
  "files",
  "website",
  "paste",
  "server",
  "transcripts",
  "queue",
  "sample",
] as const;

type Tab = (typeof TAB_KEYS)[number];

const TABS: { key: Tab; label: string }[] = [
  { key: "files", label: strings.ingestTabFiles },
  { key: "website", label: strings.ingestTabWebsite },
  { key: "paste", label: strings.ingestTabPaste },
  { key: "server", label: strings.ingestTabServerPath },
  { key: "transcripts", label: strings.ingestTabTranscripts },
  { key: "queue", label: strings.ingestTabQueue },
  { key: "sample", label: strings.ingestTabSample },
];

/**
 * The Ingest stage — one tab per source, each through the ONE pipeline:
 * files (upload), website (crawl toggle), paste text, server folder,
 * transcripts, the bundled sample knowledge, and the durable queue
 * (enqueue now, a worker drains it later). The active tab rides the
 * hash query (`#/ingest?tab=queue`), so reload and links restore it;
 * an unknown word falls back to the first tab.
 */
export default function Ingest() {
  const [param, setParam] = useQueryParam("tab", TAB_KEYS, "files");
  const tab: Tab = param ?? "files";
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
            onClick={() => setParam(entry.key)}
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
      {tab === "queue" && <QueueCard />}
      {tab === "sample" && <DemoLoadCard />}
    </div>
  );
}
