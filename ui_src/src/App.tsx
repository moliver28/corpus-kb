import { Suspense, lazy } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { Layout, Menu, Spin } from "antd";
import { strings } from "./strings";

// One lazy chunk per stage, so one stage never loads another's code.
const Setup = lazy(() => import("./pages/Setup"));
const Review = lazy(() => import("./pages/Review"));
const Ask = lazy(() => import("./pages/Ask"));
const Analyze = lazy(() => import("./pages/Analyze"));

/** A stage whose screen has not landed yet — one honest placeholder.
    Plain markup keeps antd's Card out of the shell chunk. */
function StagePlaceholder() {
  return (
    <section className="ui-placeholder">
      <h2>{strings.stagePlaceholderTitle}</h2>
      <p>{strings.stagePlaceholderBody}</p>
    </section>
  );
}

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
        <Suspense fallback={<Spin />}>
          <Routes>
            <Route path="/" element={<Navigate to="/setup" replace />} />
            <Route path="/setup" element={<Setup />} />
            <Route path="/review" element={<Review />} />
            <Route path="/ask" element={<Ask />} />
            <Route path="/analyze" element={<Analyze />} />
            {/* Stages without screens yet (Tasks 6-7) land above this
                catch-all; until then each shows the same placeholder. */}
            <Route path="*" element={<StagePlaceholder />} />
          </Routes>
        </Suspense>
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
