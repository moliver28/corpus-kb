import { Button, Card, Input } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import EvidenceList from "./EvidenceList";
import { isNotebookEnvelope, notebookEvidence, notebookSentences } from "./shapes";

/**
 * The notebook tab: grounded Q&A scoped to one research project, with a
 * retrieval-only switch (no LLM call). No verify badge here: the
 * notebook answer path runs no judge, so none is claimed.
 */
export default function NotebookCard() {
  const [projectId, setProjectId] = useState("");
  const [question, setQuestion] = useState("");
  const [retrievalOnly, setRetrievalOnly] = useState(false);
  const [hint, setHint] = useState("");
  const notebook = useOp("notebook-ask");

  async function run(): Promise<void> {
    if (projectId.trim() === "") {
      setHint(strings.askNotebookProjectRequired);
      return;
    }
    if (question.trim() === "") {
      setHint(strings.askQueryRequired);
      return;
    }
    setHint("");
    notebook.reset();
    await notebook.run({
      project_id: projectId.trim(),
      question: question.trim(),
      retrieval_only: retrievalOnly,
    });
  }

  let body: ReactNode = null;
  const result = notebook.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && isNotebookEnvelope(result.data)) {
      const env = result.data;
      const message = typeof env.message === "string" ? env.message : "";
      const note = typeof env.note === "string" ? env.note : "";
      if (env.status === "answer") {
        const sentences = notebookSentences(env);
        body = (
          <div className="ui-stack">
            <h4>{strings.citationsHeading}</h4>
            <ol className="ui-citations">
              {sentences.map((sentence, index) => (
                <li key={`${index}-${sentence.source}`}>
                  <span className="ui-citation-text">{sentence.text}</span>{" "}
                  <span className="ui-citation-source">({sentence.source})</span>
                </li>
              ))}
            </ol>
            <EvidenceList items={notebookEvidence(env)} />
          </div>
        );
      } else if (env.status === "evidence") {
        body = (
          <div className="ui-stack">
            {note !== "" && (
              <StatusBanner kind="info">
                {strings.askNoteLabel}: {note}
              </StatusBanner>
            )}
            <EvidenceList items={notebookEvidence(env)} />
          </div>
        );
      } else if (env.status === "empty") {
        body = <StatusBanner kind="warn">{message || strings.askEmptyState}</StatusBanner>;
      } else {
        body = <StatusBanner kind="error">{message || strings.errorTitle}</StatusBanner>;
      }
    } else if (result.ok) {
      body = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else {
      body = <ErrorPanel result={result} onRetry={() => void run()} />;
    }
  }

  return (
    <Card title={strings.askTabNotebook}>
      <div className="ui-stack">
        <div className="ui-actions">
          <Input
            style={{ maxWidth: 360 }}
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            placeholder={strings.askNotebookProjectLabel}
          />
          <Input
            style={{ maxWidth: 480 }}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={strings.askQueryLabel}
            onPressEnter={() => void run()}
          />
        </div>
        <div className="ui-actions">
          {/* A pressed toggle button, not a switch component: one less
              module in the shared bundle, same keyboard accessibility. */}
          <Button
            aria-pressed={retrievalOnly}
            type={retrievalOnly ? "primary" : "default"}
            onClick={() => setRetrievalOnly(!retrievalOnly)}
          >
            {strings.askNotebookRetrievalOnly}
          </Button>
          <Button type="primary" loading={notebook.running} onClick={() => void run()}>
            {strings.askNotebookAction}
          </Button>
        </div>
        {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
        {body}
      </div>
    </Card>
  );
}
