import { Button, Card } from "antd";
import { useState } from "react";
import { strings } from "../strings";
import AskCard from "./ask/AskCard";
import NotebookCard from "./ask/NotebookCard";
import SearchCard from "./ask/SearchCard";
import VerifyCard from "./ask/VerifyCard";

type Tab = "search" | "verify" | "notebook";

/**
 * The Ask stage — the cited answer, then the query switcher: search,
 * verify, and the project-scoped notebook ask. Plain buttons keep the
 * switcher light (one stage never loads another's code).
 */
export default function Ask() {
  const [tab, setTab] = useState<Tab>("search");
  return (
    <div className="ui-stack">
      <Card title={strings.askTitle}>
        <p>{strings.askBody}</p>
      </Card>
      <AskCard />
      <div className="ui-actions">
        <Button
          aria-pressed={tab === "search"}
          type={tab === "search" ? "primary" : "default"}
          onClick={() => setTab("search")}
        >
          {strings.askTabSearch}
        </Button>
        <Button
          aria-pressed={tab === "verify"}
          type={tab === "verify" ? "primary" : "default"}
          onClick={() => setTab("verify")}
        >
          {strings.askTabVerify}
        </Button>
        <Button
          aria-pressed={tab === "notebook"}
          type={tab === "notebook" ? "primary" : "default"}
          onClick={() => setTab("notebook")}
        >
          {strings.askTabNotebook}
        </Button>
      </div>
      {tab === "search" && <SearchCard />}
      {tab === "verify" && <VerifyCard />}
      {tab === "notebook" && <NotebookCard />}
    </div>
  );
}
