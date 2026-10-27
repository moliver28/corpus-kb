import { Button } from "antd";
import { useState } from "react";
import PathCard from "./PathCard";
import { strings } from "../strings";

/**
 * The bulk folder walk: one folder on the server, subfolders on or off.
 * Shared by the Ingest server-path tab and the Scale bulk card — one
 * op (ingest-directory), one form.
 */
export default function DirectoryCard({ title }: { title: string }) {
  const [recursive, setRecursive] = useState(true);
  return (
    <PathCard
      title={title}
      body={strings.setupIngestDirBody}
      label={strings.ingestDirLabel}
      requiredMessage={strings.ingestDirRequired}
      pathKey="directory_path"
      extraBody={{ recursive }}
      opId="ingest-directory"
    >
      <div className="ui-actions">
        <Button
          aria-pressed={recursive}
          type={recursive ? "primary" : "default"}
          onClick={() => setRecursive(!recursive)}
        >
          {strings.ingestRecursiveToggle}
        </Button>
      </div>
    </PathCard>
  );
}
