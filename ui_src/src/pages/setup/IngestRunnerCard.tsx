import { Card, Divider, Typography } from "antd";
import OpRunner from "../../components/OpRunner";
import { strings } from "../../strings";

/**
 * The server-side and web ingest actions, each through the ONE
 * manifest-driven OpRunner (spec D.2: paramsKnown false gives the
 * validated JSON editor; no screen-side second pipeline).
 */
export default function IngestRunnerCard() {
  return (
    <Card title={strings.setupIngestTitle}>
      <Typography.Title level={5}>{strings.setupIngestDirTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">{strings.setupIngestDirBody}</Typography.Paragraph>
      <OpRunner opId="ingest-directory" />
      <Divider />
      <Typography.Title level={5}>{strings.setupIngestUrlTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">{strings.setupIngestUrlBody}</Typography.Paragraph>
      <OpRunner opId="ingest-url" />
    </Card>
  );
}
