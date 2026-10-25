import {mkdir, mkdtemp, realpath, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {describe, expect, test, vi} from "vitest";
import type {ValidEntry} from "../src/domain.js";
import {runUpdates, type UpdateDetector, type UpdateExecutor, type UpdateStep} from "../src/updater/updater.js";
import {createSystemUpdater, type SystemUpdaterDependencies} from "../src/updater/system.js";

const entry = (id: string, realPath = `/real/${id}`): ValidEntry => ({
  id, kind: "extension", registryPath: `/registry/${id}`, realPath, launchPath: `${realPath}/index.ts`, status: "valid", diagnostics: [],
});

describe("multi-phase update orchestration", () => {
  test("runs Git then npm and gates npm on safe Git completion", async () => {
    const detected: UpdateStep[] = [
      {manager: "git", key: "git:/real/a", cwd: "/real/a"},
      {manager: "npm", key: "npm:/real/a", cwd: "/real/a"},
    ];
    const detector: UpdateDetector = async () => detected;
    const order: string[] = [];
    const executor: UpdateExecutor = async (step) => {
      order.push(step.manager);
      return step.manager === "git" ? {manager: "git", status: "up-to-date"} : {manager: "npm", status: "updated"};
    };
    const [result] = await runUpdates([entry("a")], detector, executor);
    expect(order).toEqual(["git", "npm"]);
    expect(result?.steps).toEqual([{manager: "git", status: "up-to-date"}, {manager: "npm", status: "updated"}]);
  });

  test.each([
    {manager: "git" as const, status: "skipped" as const, reason: "dirty working tree"},
    {manager: "git" as const, status: "failed" as const, reason: "pull failed"},
  ])("skips npm after Git $status", async (gitResult) => {
    const execute = vi.fn<UpdateExecutor>(async (step) => step.manager === "git" ? gitResult : {manager: "npm", status: "updated"});
    const [result] = await runUpdates([entry("a")], async () => [
      {manager: "git", key: "git:/real/a", cwd: "/real/a"}, {manager: "npm", key: "npm:/real/a", cwd: "/real/a"},
    ], execute);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(result?.steps[1]).toEqual({manager: "npm", status: "skipped", reason: "git phase did not complete safely"});
  });

  test("reports unmanaged Entries and isolates failures between Entries", async () => {
    const execute: UpdateExecutor = async (step) => step.cwd.endsWith("a")
      ? {manager: step.manager, status: "failed", reason: "failed"}
      : {manager: step.manager, status: "up-to-date"};
    const results = await runUpdates([entry("a"), entry("b"), entry("c")], async (candidate) => candidate.id === "c" ? [] : [
      {manager: "npm", key: `npm:${candidate.realPath}`, cwd: candidate.realPath},
    ], execute);
    expect(results.map(({steps}) => steps[0]?.status)).toEqual(["failed", "up-to-date", "unmanaged"]);
  });

  test("deduplicates phase mutation by manager and real path", async () => {
    const execute = vi.fn<UpdateExecutor>(async (step) => ({manager: step.manager, status: "updated"}));
    const shared = "/real/shared";
    const results = await runUpdates([entry("a", shared), entry("b", shared)], async () => [
      {manager: "git", key: `git:${shared}`, cwd: shared}, {manager: "npm", key: `npm:${shared}`, cwd: shared},
    ], execute);
    expect(execute).toHaveBeenCalledTimes(2);
    expect(results[0]?.steps).toEqual(results[1]?.steps);
  });

  test("turns detection errors into local failures and continues", async () => {
    const results = await runUpdates([entry("a"), entry("b")], async (candidate) => {
      if (candidate.id === "a") throw new Error("disappeared");
      return [];
    }, async (step) => ({manager: step.manager, status: "updated"}));
    expect(results[0]?.steps).toEqual([{manager: "local", status: "failed", reason: "disappeared"}]);
    expect(results[1]?.steps).toEqual([{manager: "local", status: "unmanaged"}]);
  });
});

describe("system update adapter", () => {
  const output = (stdout = "", code = 0, stderr = "") => ({code, stdout, stderr});

  test("detects a Git root and root package.json as independent ordered phases", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-detect-"));
    await writeFile(path.join(root, "package.json"), "{}\n");
    const canonical = await realpath(root);
    const deps: SystemUpdaterDependencies = {
      findExecutable: async (name) => name === "git" ? "/bin/git" : "/bin/npm",
      commandOutput: async (_command, args) => args.includes("--show-toplevel") ? output(`${canonical}\n`) : output(),
    };
    const updater = createSystemUpdater(deps);
    expect(await updater.detect(entry("x", canonical))).toEqual([
      {manager: "git", key: `git:${canonical}`, cwd: canonical},
      {manager: "npm", key: `npm:${canonical}`, cwd: canonical},
    ]);
  });

  test("does not treat a monorepo subdirectory as a Git root", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-subdir-"));
    const child = path.join(root, "child");
    await mkdir(child);
    await writeFile(path.join(child, "package.json"), "{}\n");
    const updater = createSystemUpdater({
      findExecutable: async (name) => name === "git" ? "/bin/git" : "/bin/npm",
      commandOutput: async () => output(`${root}\n`),
    });
    expect(await updater.detect(entry("child", child))).toEqual([
      {manager: "npm", key: `npm:${child}`, cwd: child},
    ]);
  });

  test("detects a non-Git root package.json as npm-only", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-npm-only-"));
    await writeFile(path.join(root, "package.json"), "{}\n");
    const updater = createSystemUpdater({findExecutable: async (name) => name === "npm" ? "/bin/npm" : undefined, commandOutput: async () => output()});
    expect(await updater.detect(entry("x", root))).toEqual([{manager: "npm", key: `npm:${root}`, cwd: root}]);
  });

  test("keeps a Git phase when git is missing but a linked-worktree marker exists", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-gitfile-"));
    await writeFile(path.join(root, ".git"), "gitdir: /external/worktree\n");
    const updater = createSystemUpdater({findExecutable: async () => undefined, commandOutput: async () => output()});
    expect(await updater.detect(entry("x", root))).toEqual([{manager: "git", key: `git:${root}`, cwd: root}]);
    expect(await updater.execute({manager: "git", key: `git:${root}`, cwd: root})).toEqual({manager: "git", status: "skipped", reason: "git not found"});
  });

  test("blocks npm when a Git marker cannot be verified as a worktree", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-corrupt-git-"));
    await mkdir(path.join(root, ".git"));
    await writeFile(path.join(root, "package.json"), "{}\n");
    const commandOutput = vi.fn(async (_command: string, args: string[]) => args.includes("--show-toplevel")
      ? output("", 128, "fatal: not a git repository")
      : output());
    const updater = createSystemUpdater({findExecutable: async (name) => name === "git" ? "/bin/git" : "/bin/npm", commandOutput});

    const phases = await updater.detect(entry("x", root));
    const [result] = await runUpdates([entry("x", root)], async () => phases, updater.execute);

    expect(result?.steps).toEqual([
      {manager: "git", status: "failed", reason: "could not verify Git worktree"},
      {manager: "npm", status: "skipped", reason: "git phase did not complete safely"},
    ]);
    expect(commandOutput.mock.calls.some(([, args]) => args[0] === "update")).toBe(false);
  });

  test("blocks npm when git is missing and an Entry has a Git marker", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-missing-git-"));
    await writeFile(path.join(root, ".git"), "gitdir: /missing/worktree\n");
    await writeFile(path.join(root, "package.json"), "{}\n");
    const commandOutput = vi.fn(async () => output());
    const updater = createSystemUpdater({findExecutable: async (name) => name === "npm" ? "/bin/npm" : undefined, commandOutput});

    const phases = await updater.detect(entry("x", root));
    const [result] = await runUpdates([entry("x", root)], async () => phases, updater.execute);

    expect(result?.steps).toEqual([
      {manager: "git", status: "skipped", reason: "git not found"},
      {manager: "npm", status: "skipped", reason: "git phase did not complete safely"},
    ]);
    expect(commandOutput).not.toHaveBeenCalled();
  });

  test("executes npm update in the Entry root and reports npm JSON counts", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-npm-"));
    await writeFile(path.join(root, "package.json"), "{}\n");
    const calls: Array<{command: string; args: string[]; cwd?: string}> = [];
    const updater = createSystemUpdater({
      findExecutable: async (name) => name === "npm" ? "/bin/npm" : undefined,
      commandOutput: async (command, args, cwd) => { calls.push({command, args, ...(cwd ? {cwd} : {})}); return output('{"added":0,"removed":0,"changed":2}'); },
    });
    expect(await updater.execute({manager: "npm", key: `npm:${root}`, cwd: root})).toEqual({manager: "npm", status: "updated"});
    expect(calls).toEqual([{command: "/bin/npm", args: ["update", "--json"], cwd: root}]);
  });

  test.each([
    ['{"added":0,"removed":0,"changed":0}', "up-to-date"],
    ["not json", "updated"],
  ])("maps successful npm output %s to %s", async (stdout, status) => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-npm-result-"));
    await writeFile(path.join(root, "package.json"), "{}\n");
    const updater = createSystemUpdater({findExecutable: async () => "/bin/npm", commandOutput: async () => output(stdout)});
    expect(await updater.execute({manager: "npm", key: `npm:${root}`, cwd: root})).toEqual({manager: "npm", status});
  });

  test("does not run npm if package.json disappeared", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "piw-update-no-package-"));
    const commandOutput = vi.fn(async () => output());
    const updater = createSystemUpdater({findExecutable: async () => "/bin/npm", commandOutput});
    expect(await updater.execute({manager: "npm", key: `npm:${root}`, cwd: root})).toEqual({manager: "npm", status: "failed", reason: "package.json is no longer a regular file"});
    expect(commandOutput).not.toHaveBeenCalled();
  });

  test.each([
    {label: "dirty", replies: [output("?? local\n")] as ReturnType<typeof output>[], expected: "dirty working tree"},
    {label: "detached", replies: [output(), output("", 1)] as ReturnType<typeof output>[], expected: "detached HEAD"},
    {label: "no upstream", replies: [output(), output("main\n"), output("", 1)] as ReturnType<typeof output>[], expected: "missing upstream"},
  ])("safely skips $label Git before pull", async ({replies, expected}) => {
    const queue = [...replies];
    const updater = createSystemUpdater({findExecutable: async () => "/bin/git", commandOutput: async () => queue.shift() ?? output()});
    expect(await updater.execute({manager: "git", key: "git:/entry", cwd: "/entry"})).toEqual({manager: "git", status: "skipped", reason: expected});
  });

  test("uses only pull --ff-only and compares HEAD around a safe Git update", async () => {
    const calls: string[][] = [];
    const replies = [output(), output("main\n"), output("origin/main\n"), output("old\n"), output(), output("new\n")];
    const updater = createSystemUpdater({
      findExecutable: async () => "/bin/git",
      commandOutput: async (_command, args) => { calls.push(args); return replies.shift() ?? output(); },
    });
    expect(await updater.execute({manager: "git", key: "git:/entry", cwd: "/entry"})).toEqual({manager: "git", status: "updated"});
    expect(calls).toEqual([
      ["-C", "/entry", "status", "--porcelain"],
      ["-C", "/entry", "symbolic-ref", "--quiet", "--short", "HEAD"],
      ["-C", "/entry", "rev-parse", "--abbrev-ref", "@{upstream}"],
      ["-C", "/entry", "rev-parse", "HEAD"],
      ["-C", "/entry", "pull", "--ff-only"],
      ["-C", "/entry", "rev-parse", "HEAD"],
    ]);
  });
});
