import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { Layout, Menu } from "antd";
import Setup from "./pages/Setup";
import { strings } from "./strings";

/** The eight lifecycle stages, in order. Routes are hash-based (`#/setup`). */
const STAGES = [
  { key: "setup", label: strings.stageSetup },
  { key: "ingest", label: strings.stageIngest },
  { key: "review", label: strings.stageReview },
  { key: "scale", label: strings.stageScale },
  { key: "ask", label: strings.stageAsk },
  { key: "analyze", label: strings.stageAnalyze },
  { key: "codebook", label: strings.stageCodebook },
  { key: "release", label: strings.stageRelease },
] as const;

function StageShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const selected = STAGES.find((stage) => `/${stage.key}` === location.pathname)?.key;
  return (
    <Layout className="ui-shell">
      <Layout.Header className="ui-header">
        <strong className="ui-header__title">{strings.appTitle}</strong>
        <Menu
          theme="dark"
          mode="horizontal"
          className="ui-header__menu"
          selectedKeys={selected ? [selected] : []}
          items={STAGES.map((stage) => ({ key: stage.key, label: stage.label }))}
          onClick={({ key }) => {
            navigate(`/${key}`);
          }}
        />
      </Layout.Header>
      <Layout.Content className="ui-content">
        <Routes>
          <Route path="/" element={<Navigate to="/setup" replace />} />
          <Route path="/setup" element={<Setup />} />
          {/* The remaining stages land with their screens (Tasks 6-7), each
              lazy-loaded so one stage never loads another's code. */}
        </Routes>
      </Layout.Content>
    </Layout>
  );
}

export default function App() {
  return (
    <HashRouter>
      <StageShell />
    </HashRouter>
  );
}
