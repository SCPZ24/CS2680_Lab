import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it, vi } from "vitest";
import { buildSnapshot } from "../../src/state/model.js";
import { createHudStyles, HudComponent } from "../../src/tui/hud.js";

function styled(label: string) {
  return (value: string) => `\u001b[3${label.length % 8}m${value}\u001b[0m`;
}

const styles = {
  compactBorder: vi.fn(styled("compactBorder")),
  focusedBorder: vi.fn(styled("focusedBorder")),
  additions: vi.fn(styled("additions")),
  deletions: vi.fn(styled("deletions")),
  text: vi.fn(styled("text")),
  muted: vi.fn(styled("muted")),
  section: {
    conflicts: vi.fn(styled("conflicts")),
    staged: vi.fn(styled("staged")),
    changes: vi.fn(styled("changes")),
    untracked: vi.fn(styled("untracked")),
  },
};

const snapshot = buildSnapshot({
  generation: 1,
  root: "/repo",
  branch: "feature/very-long-branch",
  hasHead: true,
  completeness: "complete",
  files: [
    {
      recordType: "1",
      path: "changed-long-file-name.ts",
      indexStatus: ".",
      worktreeStatus: "M",
      conflict: false,
    },
    {
      recordType: "?",
      path: "新文件.txt",
      indexStatus: "?",
      worktreeStatus: "?",
      conflict: false,
    },
  ],
  additions: 12,
  deletions: 3,
  binaryFileCount: 0,
});

describe("HudComponent", () => {
  it("maps every HUD semantic to a public Pi theme token", () => {
    const fg = vi.fn((_color: string, value: string) => value);
    const palette = createHudStyles({ fg } as never);

    palette.compactBorder("c");
    palette.focusedBorder("f");
    palette.additions("+1");
    palette.deletions("-1");
    palette.text("path");
    palette.muted("hint");
    palette.section.staged("M");
    palette.section.changes("M");
    palette.section.conflicts("UU");
    palette.section.untracked("?");

    expect(fg.mock.calls.map(([color]) => color)).toEqual([
      "borderMuted",
      "borderAccent",
      "toolDiffAdded",
      "toolDiffRemoved",
      "text",
      "muted",
      "success",
      "warning",
      "error",
      "accent",
    ]);
  });

  it("renders all width tiers without exceeding width", () => {
    const hud = new HudComponent({
      snapshot: () => snapshot,
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
    });
    for (const width of [52, 39, 28, 27, 12, 3]) {
      expect(
        hud.render(width).every((line) => visibleWidth(line) <= width),
      ).toBe(true);
    }
  });

  it("moves only file selection and maps public keys", () => {
    const action = vi.fn();
    const hud = new HudComponent({
      snapshot: () => snapshot,
      footer: () => "keys",
      onAction: action,
      styles,
    });
    hud.focused = true;
    hud.handleInput("j");
    expect(hud.selectedId).toContain("新文件.txt");
    hud.handleInput(" ");
    hud.handleInput("A");
    hud.handleInput("r");
    hud.handleInput("\u001b");
    expect(action.mock.calls.map(([name]) => name)).toEqual([
      "row",
      "stage-all",
      "refresh",
      "close",
    ]);
  });

  it("renders a themed compact frame and hides zero-valued stats", () => {
    const hud = new HudComponent({
      snapshot: () => ({ ...snapshot, deletions: 0 }),
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
    });

    const lines = hud.render(52);

    expect(lines).toHaveLength(3);
    expect(lines[0]).toContain("╭");
    expect(lines[0]).toContain("Worktree · feature/very-long-branch");
    expect(stripTerminalSequences(lines[1])).toMatch(/^│ 2 files/);
    expect(lines[1]).toContain("+12");
    expect(lines.join("\n")).not.toContain("-0");
    expect(lines[2]).toContain("⌘R");
    expect(lines.join("\n")).not.toContain("Ctrl");
    expect(styles.compactBorder).toHaveBeenCalled();
    expect(styles.additions).toHaveBeenCalledWith("+12");
    expect(styles.deletions).not.toHaveBeenCalledWith("-0");
  });

  it("keeps a clean Compact HUD framed with status and shortcut rows", () => {
    const cleanSnapshot = buildSnapshot({
      generation: 2,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files: [],
      additions: 0,
      deletions: 0,
      binaryFileCount: 0,
    });
    const hud = new HudComponent({
      snapshot: () => cleanSnapshot,
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
    });

    const lines = hud.render(52).map(stripTerminalSequences);

    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/^╭ Worktree · main /);
    expect(lines[1]).toMatch(/^│ clean\s+│$/);
    expect(lines[2]).toMatch(/^╰ ⌘R /);
  });

  it("styles deletions and hides the entire stats segment at zero", () => {
    const deleted = new HudComponent({
      snapshot: () => ({ ...snapshot, additions: 0, deletions: 7 }),
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
    });
    const zero = new HudComponent({
      snapshot: () => ({ ...snapshot, additions: 0, deletions: 0 }),
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
    });

    expect(deleted.render(52)[1]).toContain("-7");
    expect(styles.deletions).toHaveBeenCalledWith("-7");
    expect(stripTerminalSequences(zero.render(52)[1])).toMatch(
      /^│ 2 files\s+│$/,
    );
  });

  it("styles focused section titles and status codes but not paths", () => {
    const hud = new HudComponent({
      snapshot: () => snapshot,
      footer: () => "Space stage · A all · Enter diff",
      onAction: vi.fn(),
      styles,
    });
    hud.focused = true;

    const rendered = hud.render(52).join("\n");

    expect(rendered).toContain("Changes 1");
    expect(rendered).toContain("Untracked 1");
    expect(rendered).toContain("changed-long-file-name.ts");
    expect(rendered).not.toContain("r refresh");
    expect(styles.focusedBorder).toHaveBeenCalled();
    expect(styles.section.changes).toHaveBeenCalledWith("Changes 1");
    expect(styles.section.changes).toHaveBeenCalledWith("M");
    expect(styles.section.untracked).toHaveBeenCalledWith("Untracked 1");
    expect(styles.section.untracked).toHaveBeenCalledWith("?");
    expect(styles.text).toHaveBeenCalledWith("changed-long-file-name.ts");
  });

  it("does not close focused mode for the global open shortcuts", () => {
    const action = vi.fn();
    const hud = new HudComponent({
      snapshot: () => snapshot,
      footer: () => "keys",
      onAction: action,
      styles,
    });
    hud.focused = true;

    hud.handleInput("\u0012");
    hud.handleInput("\u001b[114;9u");

    expect(action).not.toHaveBeenCalledWith("close");
  });

  it("renders selected row and footer inside the live max-line budget", () => {
    const files = Array.from({ length: 30 }, (_, index) => ({
      recordType: "1" as const,
      path: `file-${String(index).padStart(2, "0")}.ts`,
      indexStatus: ".",
      worktreeStatus: "M",
      conflict: false,
    }));
    const crowded = buildSnapshot({
      generation: 3,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files,
    });
    const hud = new HudComponent({
      snapshot: () => crowded,
      footer: () => "FOOTER",
      onAction: vi.fn(),
      styles,
      maxLines: () => 7,
    });
    hud.focused = true;
    hud.selectedId = crowded.rows[20].id;

    const lines = hud.render(52).map(stripTerminalSequences);

    expect(lines).toHaveLength(7);
    expect(lines.some((line) => line.includes("file-20.ts"))).toBe(true);
    expect(lines.at(-1)).toContain("FOOTER");
    expect(lines[0]).toMatch(/^╭/);
    expect(lines.at(-1)).toMatch(/^╰/);
  });

  it("renders exact conflict XY codes and compact submodule markers", () => {
    const files = ["DD", "AU", "UD", "UA", "DU", "AA", "UU"].map((code) => ({
      recordType: "u" as const,
      path: `conflict-${code}`,
      indexStatus: code[0],
      worktreeStatus: code[1],
      conflict: true,
      submodule: code === "DD" ? "S.M." : "N...",
    }));
    files.push({
      recordType: "u",
      path: "vendor/component-with-a-very-long-path",
      indexStatus: "M",
      worktreeStatus: "M",
      conflict: true,
      submodule: "S.M.",
    });
    const exact = buildSnapshot({
      generation: 1,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files,
    });
    const hud = new HudComponent({
      snapshot: () => exact,
      footer: () => "keys",
      onAction: vi.fn(),
      styles,
      maxLines: () => 20,
    });
    hud.focused = true;
    const lines = hud.render(42).map(stripTerminalSequences);
    for (const code of ["AU", "UD", "UA", "DU", "AA", "UU"])
      expect(
        lines.some((line) => line.includes(`${code} conflict-${code}`)),
      ).toBe(true);
    expect(lines.some((line) => line.includes("DD S conflict-DD"))).toBe(true);
    expect(lines.some((line) => line.includes("UU S conflict-UU"))).toBe(false);
    expect(lines.every((line) => visibleWidth(line) <= 42)).toBe(true);
  });
});
