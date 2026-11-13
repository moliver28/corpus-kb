// Chat citation chips (Phase-2 Wave 7): one antd Tag per cited chunk.
// A click deep-links to the Review drawer through the Wave-5
// useQueryParam hash-query seam (?analyze=<doc_id>); an id the window
// does not serve reads as absent — honest absence, never a broken view.
import { Button, Space, Tooltip } from "antd";
import { useNavigate } from "react-router";
import type { ChatCitation } from "../../hooks/useChatSession";
import { strings } from "../../strings/chat";

function basename(label: string): string {
  const last = label.split(/[\\/]/).pop();
  return last === undefined || last === "" ? label : last;
}

export default function CitationChips({ citations }: { citations: ChatCitation[] }) {
  const navigate = useNavigate();
  if (citations.length === 0) return null;
  return (
    <Space size={4} wrap className="ui-chat-citations" aria-label={strings.chatCitationsTitle}>
      <span className="ui-chat-citations__title">{strings.chatCitationsTitle}:</span>
      {citations.map((citation) => (
        <Tooltip key={citation.chunk_id} title={citation.snippet}>
          <Button
            size="small"
            className="ui-chat-citation"
            onClick={() => navigate(`/review?analyze=${citation.document_id}`)}
          >
            {basename(citation.title)}
          </Button>
        </Tooltip>
      ))}
    </Space>
  );
}
