import { Suspense, lazy } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { Layout, Spin } from "antd";
import DeskRail from "./components/DeskRail";
import type { RailStage } from "./components/DeskRail";
import { strings } from "./strings/app";

// One lazy chunk per stage, so one stage never loads another's code.
const Setup = lazy(() => import("./pages/Setup"));
const Ingest = lazy(() => import("./pages/Ingest"));
const Review = lazy(() => import("./pages/Review"));
const Ask = lazy(() => import("./pages/Ask"));
const Analyze = lazy(() => import("./pages/Analyze"));
const Graph = lazy(() => import("./pages/Graph"));
const Codebook = lazy(() => import("./pages/Codebook"));
const Scale = lazy(() => import("./pages/Scale"));
const Release = lazy(() => import("./pages/Release"));
// The Map (Phase-2 Wave 6): a domain view, not a lifecycle stage.
// @xyflow/react rides ONLY this chunk (the ui-import-graph gate).
const Map = lazy(() => import("./pages/Map"));
const Chat = lazy(() => import("./pages/Chat"));

// The Chat (Phase-2 Wave 7): a domain view. @ant-design/x rides ONLY
// this chunk (the ui-import-graph gate). The rail entry lands with the
// real page (step 7).

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

/** The eight lifecycle stages, then the graph domain view (Wave 4).
    Routes are hash-based (`#/setup`). */
const STAGES: RailStage[] = [
  { key: "setup", label: strings.stageSetup },
  { key: "ingest", label: strings.stageIngest },
  { key: "review", label: strings.stageReview },
  { key: "scale", label: strings.stageScale },
  { key: "ask", label: strings.stageAsk },
  { key: "analyze", label: strings.stageAnalyze },
  { key: "graph", label: strings.stageGraph },
  { key: "codebook", label: strings.stageCodebook },
  { key: "release", label: strings.stageRelease },
  // The domain view rides after the lifecycle stages (D7: the rail
  // encodes the lifecycle; the Map is a view over graph data).
  { key: "map", label: strings.stageMap },
];

function StageShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const selected = STAGES.find((stage) => `/${stage.key}` === location.pathname)?.key;
  return (
    <Layout className="ui-shell">
      {/* The 48px bar carries the app title only — the stage sequence
          lives in the Desk rail, never back in a horizontal menu. */}
      <Layout.Header className="ui-header">
        {/* The app title is the page's only h1; card sections are h2. */}
        <h1 className="ui-header__title">{strings.appTitle}</h1>
      </Layout.Header>
      <Layout>
        <DeskRail
          stages={STAGES}
          selected={selected}
          onSelect={(key) => {
            navigate(`/${key}`);
          }}
        />
        <Layout.Content className="ui-content">
          <Suspense fallback={<Spin />}>
            <Routes>
              <Route path="/" element={<Navigate to="/setup" replace />} />
              <Route path="/setup" element={<Setup />} />
              <Route path="/ingest" element={<Ingest />} />
              <Route path="/review" element={<Review />} />
              <Route path="/ask" element={<Ask />} />
              <Route path="/analyze" element={<Analyze />} />
              <Route path="/graph" element={<Graph />} />
              <Route path="/codebook" element={<Codebook />} />
              <Route path="/scale" element={<Scale />} />
              <Route path="/release" element={<Release />} />
              <Route path="/map" element={<Map />} />
              <Route path="/chat" element={<Chat />} />
              {/* Stages without screens yet (Tasks 6-7) land above this
                  catch-all; until then each shows the same placeholder. */}
              <Route path="*" element={<StagePlaceholder />} />
            </Routes>
          </Suspense>
        </Layout.Content>
      </Layout>
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
