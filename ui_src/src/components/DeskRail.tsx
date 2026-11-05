import { Layout, Menu } from "antd";
import { useState } from "react";
import type { MenuProps } from "antd";
import { usePoll } from "../hooks/usePoll";
import { runsOf } from "../pages/scale/runFilter";
import { envelopeResult, isDoctorResult } from "../pages/setup/doctor";
import type { DoctorResult } from "../pages/setup/doctor";
import { strings } from "../strings";

/** One rail row of the stage sequence (the shell's route keys). */
export interface RailStage {
  key: string;
  label: string;
}

/** The three dot states the design tokens name: ring, disc, ringed disc. */
type DotState = "pending" | "active" | "done";

/**
 * The ingest stage's dot state, read from the ingest-status poll: the
 * server's own incomplete-runs count says a run is active; runs in the
 * ledger say the stage has run; an empty ledger stays pending. This is
 * the only stage with a live source today — the other dots stay
 * neutral, and no state is invented (release-status needs a version
 * id, and no corpus-level release read exists).
 */
function ingestDotState(data: unknown): DotState {
  const runs = runsOf(data);
  if (typeof data === "object" && data !== null) {
    const incomplete = (data as Record<string, unknown>).incomplete_runs;
    if (typeof incomplete === "number" && incomplete > 0) {
      return "active";
    }
  }
  return runs.length > 0 ? "done" : "pending";
}

/** One state dot: the glyph shape carries the state, never color alone. */
function StageDot({ state, title }: { state: DotState; title: string }) {
  return <span className={`ui-dot ui-dot--${state}`} title={title} aria-hidden="true" />;
}

/** One health row: the probe name, its plain state word, and a dot. */
function HealthRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <p className="ui-rail__health-row">
      <span
        className={`ui-health-dot ui-health-dot--${ok ? "ok" : "down"}`}
        aria-hidden="true"
      />
      {label} {ok ? strings.railHealthOk : strings.railHealthDown}
    </p>
  );
}

/**
 * The Desk rail (ui-design-direction.md §Layout): the corpus label and
 * the honest not-wired switcher row up top, then the eight stages in
 * order with their state dots, then the health strip at the bottom.
 * Two polls feed it: doctor (corpus label + probes) and ingest-status
   * (the ingest dot). Under a narrow viewport the Sider collapses to the
   * dot column; the dot's own hover hint keeps the stage names reachable.
   */
export default function DeskRail({
  stages,
  selected,
  onSelect,
}: {
  stages: RailStage[];
  selected: string | undefined;
  onSelect: (key: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const doctor = usePoll("doctor");
  const ingest = usePoll("ingest-status");
  const inner =
    doctor.result !== null && doctor.result.ok
      ? envelopeResult(doctor.result.data)
      : undefined;
  const health: DoctorResult | null = isDoctorResult(inner) ? inner : null;
  const ingestState = ingestDotState(ingest.result?.data);

  const items: MenuProps["items"] = stages.map((stage) => {
    const state: DotState = stage.key === "ingest" ? ingestState : "pending";
    const stateWord =
      state === "active"
        ? strings.railStateActive
        : state === "done"
          ? strings.railStateDone
          : strings.railStatePending;
    return {
      key: stage.key,
      icon: <StageDot state={state} title={`${stage.label} — ${stateWord}`} />,
      label: stage.label,
    };
  });

  return (
    <Layout.Sider
      className="ui-rail"
      width={232}
      collapsedWidth={64}
      breakpoint="lg"
      collapsed={collapsed}
      onCollapse={setCollapsed}
    >
      <div className="ui-rail__corpus">
        {health !== null && (
          <p className="ui-rail__corpus-name">
            {strings.railCorpusName.replace("{name}", health.profile.profile)}
          </p>
        )}
        <p className="ui-rail__switcher">{strings.railSwitcherNotWired}</p>
      </div>
      {/* One hover hint per dot: the dot's own title. antd's collapsed
          Tooltip is off, so a collapsed hover shows one hint, not two. */}
      <Menu
        mode="inline"
        className="ui-rail__menu"
        selectedKeys={selected ? [selected] : []}
        items={items}
        tooltip={false}
        onClick={({ key }) => onSelect(key)}
      />
      <div className="ui-rail__health">
        <p className="ui-rail__health-title">{strings.railHealthTitle}</p>
        {health === null ? (
          <p className="ui-rail__health-row">{strings.railHealthWaiting}</p>
        ) : (
          <>
            <HealthRow label={strings.railHealthDb} ok={health.postgres.reachable} />
            <HealthRow label={strings.railHealthModels} ok={health.ollama.reachable} />
          </>
        )}
      </div>
    </Layout.Sider>
  );
}
