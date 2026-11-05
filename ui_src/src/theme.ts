import type { ThemeConfig } from "antd";
import { theme } from "antd";

// The design identity (ui-design-direction.md): petrol accents on a
// cool grey-green ground, madder reserved for citation marks and
// errors. cssVar turns the tokens into --ckb-* variables, which ui.css
// reads instead of hard-coded hex.
//
// antd durations are JS constants, so the prefers-reduced-motion media
// query alone cannot reach them; the motion token turns off with them.
const prefersReducedMotion =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export const corpusKbTheme: ThemeConfig = {
  cssVar: { prefix: "ckb" }, // v6 CSS variables; ui.css reads var(--ckb-*)
  algorithm: theme.defaultAlgorithm, // swap to theme.darkAlgorithm for dark mode
  token: {
    // Seed tokens — antd derives hover/active/bg tints from these
    colorPrimary: "#14555B", // Petrol
    colorInfo: "#14555B",
    colorSuccess: "#2E7D32",
    colorWarning: "#9A6A00",
    colorError: "#9E3B2C", // Madder
    colorLink: "#14555B",
    borderRadius: 6,
    fontFamily:
      '"IBM Plex Sans", -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    fontFamilyCode: '"IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace',
    fontSize: 14,
    controlHeight: 32,
    motion: !prefersReducedMotion,
    motionUnit: 0.1,
    motionEaseOutQuint: "cubic-bezier(0.23, 1, 0.32, 1)",
    // Map-token overrides (docs advise against setting colorTextBase/colorBgBase directly)
    colorBgLayout: "#F2F4F1", // Field
    colorBorder: "#C7D0CC",
    colorBorderSecondary: "#DDE3DF",
    colorText: "#232827", // Ink
    colorTextHeading: "#232827",
    colorTextSecondary: "#55605D", // Graphite, 6.5:1
    colorTextTertiary: "#55605D",
    colorTextDescription: "#55605D", // antd default rgba(0,0,0,.45) fails AA
    fontSizeSM: 12, // pinned scale: 12/14/16/20/24/32
    fontSizeLG: 16,
    fontSizeXL: 20,
    fontSizeHeading1: 32,
    fontSizeHeading2: 24,
    fontSizeHeading3: 20,
    fontSizeHeading4: 16,
    fontSizeHeading5: 14,
    lineHeight: 1.55,
    motionDurationFast: "0.1s",
    motionDurationMid: "0.2s",
    motionDurationSlow: "0.24s",
    borderRadiusLG: 10, // panels/modals/drawers — radius hierarchy vs controls' 6
  },
  components: {
    Layout: { headerBg: "#FFFFFF", headerHeight: 48, siderBg: "#F2F4F1", bodyBg: "#F2F4F1" },
    Menu: {
      itemBg: "transparent",
      subMenuItemBg: "transparent",
      activeBarBorderWidth: 0,
      // The rail's selected stage: antd derives itemSelectedBg from the
      // primary, and petrol's derived tint lands mid-grey (#929c9b) —
      // petrol text there reads 3:1. This hand-mixed wash (12% petrol
      // over white) keeps the petrol text at 7.3:1, AA and above.
      itemSelectedBg: "#E3EEEE",
      itemSelectedColor: "#14555B",
    },
    Table: {
      // density for data-heavy tables; global compactAlgorithm stays off
      cellPaddingBlock: 8,
      cellPaddingInline: 12,
      cellPaddingBlockSM: 6,
      cellPaddingInlineSM: 8,
      fontSizeSM: 12,
      headerBg: "#F7F9F7",
      // Empty-table placeholder rows ride the map token colorTextDisabled
      // (antd default rgba(0,0,0,.25) = #bfbfbf fails AA at 1.83:1). Empty
      // text is real content, so it gets the Graphite tone, not a faint one.
      colorTextDisabled: "#55605D",
    },
  },
};
