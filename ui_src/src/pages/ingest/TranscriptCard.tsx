import PathCard from "../../components/PathCard";
import { strings } from "../../strings";

/**
 * The transcript ingest: one transcript file on the server, run through
 * the research transcript pipeline (turns, roles, exchanges).
 */
export default function TranscriptCard() {
  return (
    <PathCard
      title={strings.ingestTabTranscripts}
      label={strings.ingestTranscriptPathLabel}
      requiredMessage={strings.ingestTranscriptPathRequired}
      pathKey="file_path"
      opId="research-ingest-transcript"
    />
  );
}
