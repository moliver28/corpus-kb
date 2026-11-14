import { Button, Card } from "antd";
import { strings } from "../strings/ask";
import { useQueryParam } from "../hooks/useQueryParam";
import AskCard from "./ask/AskCard";
import NotebookCard from "./ask/NotebookCard";
import SearchCard from "./ask/SearchCard";
import VerifyCard from "./ask/VerifyCard";

// The evidence voice: Source Serif 4 loads only in this stage chunk,
// where the only evidence text renders (answer, citations, notebook).
import "@fontsource/source-serif-4/latin-400.css";
import "@fontsource/source-serif-4/latin-600.css";
import "@fontsource/source-serif-4/latin-400-italic.css";

const TAB_KEYS = ["search", "verify", "notebook"] as const;

type Tab = (typeof TAB_KEYS)[number];

const TAB_LABELS: Record<Tab, string> = {
  search: strings.askTabSearch,
  verify: strings.askTabVerify,
  notebook: strings.askTabNotebook,
};

/**
 * The Ask stage — the cited answer, then the query switcher: search,
 * verify, and the project-scoped notebook ask. The active tab rides
 * the hash query (`#/ask?tab=verify`), so reload and links restore it;
 * an unknown word falls back to the search tab. Plain buttons keep the
 * switcher light (one stage never loads another's code).
 */
export default function Ask() {
  const [param, setParam] = useQueryParam("tab", TAB_KEYS, "search");
  const tab: Tab = param ?? "search";
  return (
    <div className="ui-stack">
      <Card title={strings.askTitle}>
        <p>{strings.askBody}</p>
      </Card>
      <AskCard />
      <div className="ui-actions">
        {TAB_KEYS.map((key) => (
          <Button
            key={key}
            aria-pressed={tab === key}
            type={tab === key ? "primary" : "default"}
            onClick={() => setParam(key)}
          >
            {TAB_LABELS[key]}
          </Button>
        ))}
      </div>
      {tab === "search" && <SearchCard />}
      {tab === "verify" && <VerifyCard />}
      {tab === "notebook" && <NotebookCard />}
    </div>
  );
}
