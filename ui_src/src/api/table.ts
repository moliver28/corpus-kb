import type { TablePaginationConfig } from "antd/es/table";

/**
 * One small capped page for result tables: the pager hides until the
 * rows need it, and the size changer never shows (the cap is the
 * honest page, not a choice). Every capped table shares this one
 * config, so no table renders its rows unbounded.
 */
export function pageConfig(pageSize: number): TablePaginationConfig {
  return { pageSize, hideOnSinglePage: true, showSizeChanger: false };
}
