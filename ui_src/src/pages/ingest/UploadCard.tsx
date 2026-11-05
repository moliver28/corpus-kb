import { Button, Card, Typography, Upload } from "antd";
import type { RcFile } from "antd/es/upload/interface";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import ReceiptCard from "../../components/ReceiptCard";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { sendMultipart } from "../../api/client";
import { runtimeConfig } from "../../api/runtimeConfig";
import type { OpResult } from "../../api/types";
import { strings } from "../../strings";

// The multipart upload route is not a JSON op, so it has no op-manifest
// row (screens must not change the registry). The path mirrors the
// server route ingest_upload_route; the response is the standard file
// ingest envelope, normalized through the shared error model in
// api/client.ts (sendMultipart caps the wait, so a hung server cannot
// spin the button forever; an expired wait reads as network).
const UPLOAD_PATH = "/api/ingest/upload";

function capBytes(): number {
  return runtimeConfig().maxUploadMb * 1024 * 1024;
}

/**
 * The upload action: pick files, check the runtime cap before sending,
 * then show the ingest receipt (chunks, entities, or the typed error).
 */
export default function UploadCard() {
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<OpResult | null>(null);
  const [note, setNote] = useState("");
  const capMb = runtimeConfig().maxUploadMb;

  function pick(f: RcFile): false {
    setFile(f);
    setResult(null);
    setNote("");
    return false; // no auto-upload; the Upload button sends
  }

  function clear(): void {
    setFile(null);
    setResult(null);
    setNote("");
  }

  async function send(): Promise<void> {
    if (file === null) {
      setNote(strings.setupUploadPickFirst);
      return;
    }
    if (file.size > capBytes()) {
      setNote(strings.setupUploadTooLarge.replace("{mb}", String(capMb)));
      return;
    }
    setNote("");
    setSending(true);
    // The card assembles the multipart body; api/client.ts sends it.
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await sendMultipart(UPLOAD_PATH, form);
    setSending(false);
    setResult(res);
  }

  let resultView = null;
  if (result !== null) {
    if (result.kind === "unavailable") {
      resultView = <UnavailablePanel result={result} onRetry={() => void send()} />;
    } else if (result.kind === "unknown") {
      // An envelope without a status word is not a receipt: show the
      // honest unreadable panel, never a "Done" headline.
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else if (result.ok && result.kind !== "network") {
      resultView = <ReceiptCard result={result} />;
    } else {
      resultView = <ErrorPanel result={result} onRetry={() => void send()} />;
    }
  }

  return (
    <Card title={strings.setupUploadTitle}>
      <Typography.Paragraph type="secondary">{strings.setupUploadBody}</Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        {strings.setupUploadCap.replace("{mb}", String(capMb))}
      </Typography.Paragraph>
      <div className="ui-actions">
        <Upload beforeUpload={pick} onRemove={clear} maxCount={1}>
          <Button>{strings.setupUploadPick}</Button>
        </Upload>
        {/* Enabled with a hint banner, like every other form: the empty
            submit says what is missing instead of sleeping silently. */}
        <Button type="primary" loading={sending} onClick={() => void send()}>
          {strings.setupUploadAction}
        </Button>
      </div>
      {note !== "" && <StatusBanner kind="warn">{note}</StatusBanner>}
      {resultView}
    </Card>
  );
}
