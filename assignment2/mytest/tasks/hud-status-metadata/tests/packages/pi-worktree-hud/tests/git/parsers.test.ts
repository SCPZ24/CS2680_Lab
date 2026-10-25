import { describe, expect, it } from "vitest";
import { NumstatParser, PorcelainV2Parser } from "../../src/git/parsers.js";

function chunks(value: string, sizes: number[]): Uint8Array[] {
  const bytes = new TextEncoder().encode(value);
  const result: Uint8Array[] = [];
  let offset = 0;
  for (const size of sizes) {
    result.push(bytes.slice(offset, offset + size));
    offset += size;
  }
  if (offset < bytes.length) result.push(bytes.slice(offset));
  return result;
}

describe("PorcelainV2Parser", () => {
  it("streams branch headers and every supported record across arbitrary chunks", () => {
    const input = [
      "# branch.oid 0123456789abcdef\n",
      "# branch.head main\n",
      "1 M. N... 100644 100644 100644 aaaaaaa bbbbbbb staged ü.txt\0",
      "1 .D N... 100644 100644 000000 aaaaaaa aaaaaaa changed\nname.txt\0",
      "2 R. N... 100644 100644 100644 aaaaaaa bbbbbbb R100 new\tname.txt\0old name.txt\0",
      "u UU N... 100644 100644 100644 100644 aaaaaaa bbbbbbb ccccccc conflict.txt\0",
      "? untracked space.txt\0",
    ].join("");
    const parser = new PorcelainV2Parser();
    for (const chunk of chunks(input, [1, 2, 3, 5, 8, 13, 21]))
      parser.push(chunk);
    const parsed = parser.finish();

    expect(parsed.branch).toBe("main");
    expect(parsed.hasHead).toBe(true);
    expect(parsed.completeness).toBe("complete");
    expect(parsed.files).toHaveLength(5);
    expect(parsed.files[2]).toMatchObject({
      path: "new\tname.txt",
      originalPath: "old name.txt",
      indexStatus: "R",
    });
    expect(parsed.files[3].conflict).toBe(true);
    expect(parsed.files[4]).toMatchObject({
      path: "untracked space.txt",
      recordType: "?",
    });
  });

  it("represents detached and unborn branches", () => {
    const detached = new PorcelainV2Parser();
    detached.push(
      new TextEncoder().encode(
        "# branch.oid abcdef123456\n# branch.head (detached)\n",
      ),
    );
    expect(detached.finish()).toMatchObject({
      branch: "detached@abcdef1",
      hasHead: true,
    });

    const unborn = new PorcelainV2Parser();
    unborn.push(
      new TextEncoder().encode("# branch.oid (initial)\n# branch.head main\n"),
    );
    expect(unborn.finish()).toMatchObject({ branch: "unborn", hasHead: false });
  });

  it("accepts NUL-terminated headers emitted by status -z", () => {
    const parser = new PorcelainV2Parser();
    parser.push(
      new TextEncoder().encode(
        "# branch.oid (initial)\0# branch.head main\0? new.txt\0",
      ),
    );
    expect(parser.finish()).toMatchObject({
      branch: "unborn",
      hasHead: false,
      recordCount: 1,
    });
  });

  it("keeps unknown XY, but makes unknown and incomplete records non-writable", () => {
    const parser = new PorcelainV2Parser();
    parser.push(
      new TextEncoder().encode(
        "1 ZQ N... 100644 100644 100644 a b weird.txt\0x opaque\0",
      ),
    );
    const parsed = parser.finish();
    expect(parsed.files[0]).toMatchObject({
      indexStatus: "Z",
      worktreeStatus: "Q",
    });
    expect(parsed.completeness).toBe("partial");

    const incomplete = new PorcelainV2Parser();
    incomplete.push(new TextEncoder().encode("? missing-nul"));
    expect(incomplete.finish().completeness).toBe("truncated");
  });

  it("caps record count without losing parsed records", () => {
    const parser = new PorcelainV2Parser({ maxRecords: 1 });
    parser.push(new TextEncoder().encode("? one\0? two\0"));
    expect(parser.finish()).toMatchObject({
      completeness: "truncated",
      recordCount: 1,
    });
  });

  it("preserves every unmerged XY code and porcelain submodule field", () => {
    const parser = new PorcelainV2Parser();
    const xy = ["DD", "AU", "UD", "UA", "DU", "AA", "UU"];
    parser.push(
      new TextEncoder().encode(
        xy
          .map(
            (code, index) =>
              `u ${code} ${index === 0 ? "S.M." : "N..."} 100644 100644 100644 100644 a b c conflict-${code}\0`,
          )
          .join(""),
      ),
    );
    const parsed = parser.finish();
    expect(
      parsed.files.map((file) => `${file.indexStatus}${file.worktreeStatus}`),
    ).toEqual(xy);
    expect(parsed.files[0]?.submodule).toBe("S.M.");
  });
});

describe("NumstatParser", () => {
  it("streams text, binary, and renamed paths", () => {
    const parser = new NumstatParser();
    const input = [
      "12\t3\tplain.txt",
      "\0",
      "-\t-\tbinary.dat",
      "\0",
      "5\t1\t",
      "\0",
      "old\nname",
      "\0",
      "new name",
      "\0",
    ].join("");
    for (const chunk of chunks(input, [2, 1, 7, 3])) parser.push(chunk);
    expect(parser.finish()).toEqual({
      additions: 17,
      deletions: 4,
      binaryFileCount: 1,
      entries: [
        { additions: 12, deletions: 3, path: "plain.txt" },
        { additions: null, deletions: null, path: "binary.dat" },
        {
          additions: 5,
          deletions: 1,
          path: "new name",
          originalPath: "old\nname",
        },
      ],
      truncated: false,
    });
  });
});
