import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Component } from "@earendil-works/pi-tui";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GitRepository } from "../../src/git/repository.js";
import { runBoundedProcess } from "../../src/git/runner.js";
import { SessionRuntime } from "../../src/runtime/session.js";

async function dirtyRepository(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "hud-session-"));
  for (const args of [
    ["init", "-q", "-b", "main"],
    ["config", "user.email", "hud@example.test"],
    ["config", "user.name", "HUD Test"],
  ]) {
    const result = await runBoundedProcess("git", args, {
      cwd: root,
      timeoutMs: 5_000,
      maxBytes: 100_000,
    });
    expect(result.exitCode, result.stderr).toBe(0);
  }
  await writeFile(join(root, "changed.txt"), "changed\n");
  return root;
}

function fakeContext(cwd: string) {
  const components: Component[] = [];
  const handles: Array<{
    focus: ReturnType<typeof vi.fn>;
    unfocus: ReturnType<typeof vi.fn>;
    hide: ReturnType<typeof vi.fn>;
    isFocused: () => boolean;
  }> = [];
  const ui = {
    notify: vi.fn(),
    custom: vi.fn(
      (
        factory: (...args: never[]) => Component,
        options: { onHandle?: (handle: unknown) => void },
      ) => {
        let resolve: (() => void) | undefined;
        const promise = new Promise<void>((done) => (resolve = done));
        const tui = { terminal: { rows: 24 }, requestRender: vi.fn() };
        const theme = { fg: (_color: string, value: string) => value };
        const component = factory(
          tui as never,
          theme as never,
          undefined as never,
          (() => resolve?.()) as never,
        );
        components.push(component);
        let focused = false;
        const handle = {
          focus: vi.fn(() => (focused = true)),
          unfocus: vi.fn(() => (focused = false)),
          hide: vi.fn(() => resolve?.()),
          isFocused: () => focused,
        };
        handles.push(handle);
        options.onHandle?.(handle);
        return promise;
      },
    ),
  };
  return {
    ctx: {
      mode: "tui",
      cwd,
      signal: new AbortController().signal,
      ui,
    } as never,
    components,
    handles,
    ui,
  };
}

describe("SessionRuntime hidden agent lifecycle", () => {
  afterEach(() => vi.restoreAllMocks());

  it("blocks staging when an edit starts while the HUD is hidden", async () => {
    const cwd = await dirtyRepository();
    const { ctx, components } = fakeContext(cwd);
    const mutate = vi.spyOn(GitRepository.prototype, "mutate");
    const runtime = await SessionRuntime.start(ctx);
    expect(runtime).toBeDefined();

    runtime?.hide();
    runtime?.toolStart("edit-hidden", "edit");
    runtime?.show(true);

    const focused = components.at(-1);
    const frame = focused?.render(52).join("\n") ?? "";
    expect(frame).toContain("Agent mutation lock (1)");
    focused?.handleInput?.(" ");
    await Promise.resolve();
    expect(mutate).not.toHaveBeenCalled();
    runtime?.dispose();
  });

  it("clears settled hidden tool ids but keeps staging gated for refresh", async () => {
    const cwd = await dirtyRepository();
    const { ctx, components } = fakeContext(cwd);
    const runtime = await SessionRuntime.start(ctx);
    runtime?.hide();
    runtime?.toolStart("write-hidden", "write");
    runtime?.clearAgentLocks();
    expect(runtime?.status()).toContain("agent mutations: 0");
    runtime?.show(true);
    expect(components.at(-1)?.render(52).join("\n")).toMatch(
      /Refreshing|refresh/i,
    );
    runtime?.dispose();
  });
});
