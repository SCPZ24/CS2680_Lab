import { describe, expect, it, vi } from "vitest";
import {
  MutationQueue,
  type MutationRequest,
} from "../../src/runtime/mutations.js";
import type { WorktreeSnapshot } from "../../src/state/model.js";

const clean = (generation = 1): WorktreeSnapshot => ({
  generation,
  root: "/repo",
  branch: "main",
  hasHead: true,
  files: [],
  rows: [],
  uniqueFileCount: 0,
  sectionCounts: { conflicts: 0, staged: 0, changes: 0, untracked: 0 },
  additions: 0,
  deletions: 0,
  binaryFileCount: 0,
  completeness: "complete",
  freshness: "current",
  capturedAt: generation,
});

describe("MutationQueue", () => {
  it("executes FIFO with a successful refresh between writes", async () => {
    const events: string[] = [];
    let current = clean(1);
    const queue = new MutationQueue({
      snapshot: () => current,
      execute: async (request) => {
        events.push(`write:${request.id}`);
      },
      refresh: async () => {
        current = clean(current.generation + 1);
        events.push("refresh");
        return true;
      },
      validate: async () => true,
    });
    const all = (id: string): MutationRequest => ({
      id,
      kind: "stage-all",
      generation: current.generation,
    });
    queue.enqueue(all("a"));
    queue.enqueue({ ...all("b"), generation: 2 });
    await queue.idle();
    expect(events).toEqual(["write:a", "refresh", "write:b", "refresh"]);
    expect(queue.maxConcurrent).toBe(1);
  });

  it("rejects during agent lock, pauses queued work, and resumes only after refresh", async () => {
    let release: (() => void) | undefined;
    const execute = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const refresh = vi.fn().mockResolvedValue(true);
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute,
      refresh,
      validate: async () => true,
    });
    queue.lockAgent("tool-1");
    expect(queue.enqueue({ id: "x", kind: "stage-all", generation: 1 })).toBe(
      false,
    );
    queue.unlockAgent("tool-1");
    expect(refresh).toHaveBeenCalledTimes(1);
    await queue.idle();
    queue.enqueue({ id: "a", kind: "stage-all", generation: 1 });
    await Promise.resolve();
    queue.lockAgent("tool-2");
    queue.enqueue({ id: "b", kind: "stage-all", generation: 1 });
    release?.();
    await Promise.resolve();
    expect(execute).toHaveBeenCalledTimes(1);
    queue.unlockAgent("tool-2");
    await queue.idle();
    expect(refresh).toHaveBeenCalled();
  });

  it("skips stale all requests and invalid file requests", async () => {
    const skipped: string[] = [];
    const execute = vi.fn();
    const queue = new MutationQueue({
      snapshot: () => clean(2),
      execute,
      refresh: async () => true,
      validate: async () => false,
      onSkip: (request) => skipped.push(request.id),
    });
    queue.enqueue({ id: "all", kind: "stage-all", generation: 1 });
    queue.enqueue({
      id: "file",
      kind: "stage-file",
      generation: 2,
      path: "x",
      rowId: "untracked\0x\0",
    });
    await queue.idle();
    expect(execute).not.toHaveBeenCalled();
    expect(skipped).toEqual(["all", "file"]);
  });

  it("stays locked after a failed agent refresh until a later success", async () => {
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute: async () => undefined,
      refresh: async () => false,
      validate: async () => true,
    });
    queue.lockAgent("tool");
    queue.unlockAgent("tool");
    await queue.idle();
    expect(
      queue.enqueue({ id: "blocked", kind: "stage-all", generation: 1 }),
    ).toBe(false);
    queue.refreshSucceeded();
    expect(
      queue.enqueue({ id: "ready", kind: "stage-all", generation: 1 }),
    ).toBe(true);
  });

  it("contains validation failures without rejecting the pump", async () => {
    const errors: string[] = [];
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute: async () => undefined,
      refresh: async () => true,
      validate: async () => {
        throw new Error("lstat failed");
      },
      onError: (error) => errors.push(error),
    });
    queue.enqueue({
      id: "file",
      kind: "stage-file",
      generation: 1,
      path: "file",
      rowId: "changes\0file\0",
    });
    await queue.idle();
    expect(errors).toEqual(["lstat failed"]);
  });

  it("keeps hidden agent work locked until a later successful refresh", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const execute = vi.fn();
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute,
      refresh,
      validate: async () => true,
    });

    queue.lockAgent("hidden-write");
    queue.unlockAgent("hidden-write", false);

    expect(refresh).not.toHaveBeenCalled();
    expect(queue.agentRefreshPending).toBe(true);
    expect(
      queue.enqueue({ id: "stale", kind: "stage-all", generation: 1 }),
    ).toBe(false);

    queue.requestAgentRefresh();
    await queue.idle();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(queue.agentRefreshPending).toBe(false);
    expect(
      queue.enqueue({ id: "fresh", kind: "stage-all", generation: 1 }),
    ).toBe(true);
    await queue.idle();
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it("retains parallel tool locks until the final tool ends and refresh succeeds", async () => {
    let resolveRefresh: ((value: boolean) => void) | undefined;
    const refresh = vi.fn(
      () => new Promise<boolean>((resolve) => (resolveRefresh = resolve)),
    );
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute: async () => undefined,
      refresh,
      validate: async () => true,
    });
    queue.lockAgent("edit-a");
    queue.lockAgent("bash-b");
    queue.unlockAgent("edit-a");
    expect(queue.agentLockCount).toBe(1);
    expect(refresh).not.toHaveBeenCalled();
    queue.unlockAgent("bash-b");
    expect(queue.agentLockCount).toBe(0);
    expect(queue.agentRefreshPending).toBe(true);
    expect(
      queue.enqueue({ id: "blocked", kind: "stage-all", generation: 1 }),
    ).toBe(false);
    resolveRefresh?.(true);
    await queue.idle();
    expect(queue.agentRefreshPending).toBe(false);
  });

  it("settles hidden agent lifecycle without leaving ghost tool locks", () => {
    const queue = new MutationQueue({
      snapshot: () => clean(),
      execute: async () => undefined,
      refresh: async () => true,
      validate: async () => true,
    });
    queue.lockAgent("orphan");
    queue.clearAgentLocks(false);
    expect(queue.agentLockCount).toBe(0);
    expect(queue.agentRefreshPending).toBe(true);
  });
});
