import { describe, expect, it } from "vitest";
import type { StatusFile } from "../../src/git/types.js";
import {
  buildSnapshot,
  type HudRow,
  reconcileSelection,
  sectionWindow,
} from "../../src/state/model.js";

const file = (overrides: Partial<StatusFile>): StatusFile => ({
  recordType: "1",
  path: "file.txt",
  indexStatus: ".",
  worktreeStatus: ".",
  conflict: false,
  ...overrides,
});

describe("snapshot rows", () => {
  it("uses fixed sections, emits dual rows, and de-duplicates conflicts", () => {
    const snapshot = buildSnapshot({
      generation: 4,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files: [
        file({ path: "both.txt", indexStatus: "M", worktreeStatus: "M" }),
        file({ path: "staged.txt", indexStatus: "A" }),
        file({ path: "changed.txt", worktreeStatus: "D" }),
        file({
          recordType: "?",
          path: "new.txt",
          indexStatus: "?",
          worktreeStatus: "?",
        }),
        file({
          recordType: "u",
          path: "conflict.txt",
          indexStatus: "U",
          worktreeStatus: "U",
          conflict: true,
        }),
      ],
    });

    expect(
      snapshot.rows.map(({ section, path }) => `${section}:${path}`),
    ).toEqual([
      "conflicts:conflict.txt",
      "staged:both.txt",
      "staged:staged.txt",
      "changes:both.txt",
      "changes:changed.txt",
      "untracked:new.txt",
    ]);
    expect(snapshot.uniqueFileCount).toBe(5);
    expect(snapshot.sectionCounts).toEqual({
      conflicts: 1,
      staged: 2,
      changes: 2,
      untracked: 1,
    });
  });

  it("uses deterministic Unicode ordering and rename identity", () => {
    const snapshot = buildSnapshot({
      generation: 1,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files: [
        file({ path: "a.txt", indexStatus: "A" }),
        file({ path: "B.txt", indexStatus: "A" }),
        file({ path: "ä.txt", indexStatus: "A" }),
        file({
          recordType: "2",
          path: "new.txt",
          originalPath: "old.txt",
          indexStatus: "R",
        }),
      ],
    });
    expect(snapshot.rows.map((row) => row.path)).toEqual([
      "B.txt",
      "a.txt",
      "new.txt",
      "ä.txt",
    ]);
    expect(snapshot.rows[2].label).toBe("old.txt → new.txt");
    expect(snapshot.rows[2].id).toContain("staged\0new.txt\0old.txt");
  });

  it("preserves submodule metadata on staged and changed rows", () => {
    const rows = buildSnapshot({
      generation: 1,
      root: "/repo",
      branch: "main",
      hasHead: true,
      completeness: "complete",
      files: [
        file({
          path: "vendor/component",
          indexStatus: "M",
          worktreeStatus: "M",
          submodule: "S.M.",
        }),
      ],
    }).rows;
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.submodule)).toEqual(["S.M.", "S.M."]);
  });
});

describe("selection", () => {
  const row = (section: HudRow["section"], path: string): HudRow => ({
    id: `${section}\0${path}\0`,
    section,
    path,
    label: path,
    indexStatus: section === "staged" ? "M" : ".",
    worktreeStatus: section === "changes" ? "M" : ".",
    conflict: false,
  });

  it("keeps row id, then nearest row in section, then next non-empty section", () => {
    const oldRows = [
      row("staged", "a"),
      row("staged", "b"),
      row("changes", "c"),
    ];
    expect(reconcileSelection(oldRows, oldRows, oldRows[1].id)).toBe(
      oldRows[1].id,
    );

    const sameSection = [
      row("staged", "a"),
      row("staged", "z"),
      row("changes", "c"),
    ];
    expect(reconcileSelection(oldRows, sameSection, oldRows[1].id)).toBe(
      sameSection[1].id,
    );

    const nextSection = [row("changes", "c"), row("untracked", "d")];
    expect(reconcileSelection(oldRows, nextSection, oldRows[1].id)).toBe(
      nextSection[0].id,
    );
  });

  it("returns a viewport centered around selection without dropping logical rows", () => {
    const rows = Array.from({ length: 20 }, (_, index) =>
      row("changes", `${index}`),
    );
    const window = sectionWindow(rows, rows[15].id, 5);
    expect(window.rows).toHaveLength(5);
    expect(window.rows.some((candidate) => candidate.id === rows[15].id)).toBe(
      true,
    );
    expect(window.hiddenBefore + window.rows.length + window.hiddenAfter).toBe(
      20,
    );
  });
});
