// TagAnalyzer pure-logic tests (no API calls). Run: npm test
const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { TagAnalyzer, PRESERVED_TAGS } = require("../lib/tag-analyzer");

const DIRS = [];
function tmpdir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ownwords-tag-"));
  DIRS.push(dir);
  return dir;
}
afterEach(() => {
  for (const dir of DIRS.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

function analyzer(overrides = {}) {
  return new TagAnalyzer({ contentDirs: [], outputDir: tmpdir(), ...overrides });
}

describe("constructor", () => {
  it("applies defaults", () => {
    const a = new TagAnalyzer({});
    assert.deepEqual(a.contentDirs, []);
    assert.equal(a.verbose, false);
    assert.equal(a._claudeClient, null);
  });

  it("lazily creates one client", () => {
    const a = analyzer();
    a._claudeClient = { stub: true };
    assert.equal(a._getClient().stub, true);
  });
});

describe("preserved tags", () => {
  it("keeps featured out of replacement", () => {
    assert.ok(PRESERVED_TAGS.includes("featured"));
  });
});

describe("_parseFrontMatter", () => {
  let a;
  beforeEach(() => {
    a = analyzer();
  });

  it("splits front matter from body", () => {
    const { frontMatter, body } = a._parseFrontMatter(
      "---\ntitle: Hello\n---\nBody text.\n"
    );
    assert.equal(frontMatter.title, "Hello");
    assert.equal(body, "Body text.\n");
  });

  it("parses lists and strips quotes", () => {
    const { frontMatter } = a._parseFrontMatter(
      '---\ntitle: "Hi"\ntags:\n  - "a"\n  - b\n---\nX\n'
    );
    assert.equal(frontMatter.title, "Hi");
    assert.deepEqual(frontMatter.tags, ["a", "b"]);
  });

  it("returns empty front matter without a block", () => {
    const { frontMatter, body } = a._parseFrontMatter("Just body.\n");
    assert.deepEqual(frontMatter, {});
    assert.equal(body, "Just body.\n");
  });
});

describe("_updateFrontMatter", () => {
  let a;
  beforeEach(() => {
    a = analyzer();
  });

  it("replaces tags and sorts them", () => {
    const out = a._updateFrontMatter(
      "---\ntitle: T\ntags:\n  - old\n---\nBody\n",
      { tags: ["zebra", "apple"] }
    );
    assert.match(out, /- "apple"\n  - "zebra"/);
    assert.doesNotMatch(out, /old/);
    assert.match(out, /Body\n$/);
  });

  it("replaces a mid-matter tags block without touching neighbors", () => {
    const out = a._updateFrontMatter(
      "---\ntitle: T\ntags:\n  - old\ncategories:\n  - keep\n---\nB\n",
      { tags: ["new"] }
    );
    assert.match(out, /- "new"/);
    assert.doesNotMatch(out, /old/);
    assert.match(out, /- keep/);
  });

  it("replaces a trailing categories block", () => {
    const out = a._updateFrontMatter("---\ntitle: T\ncategories:\n  - old\n---\nB\n", {
      category: "craft",
    });
    assert.match(out, /- "craft"/);
    assert.doesNotMatch(out, /- old/);
  });

  it("preserves an existing featured tag", () => {
    const out = a._updateFrontMatter(
      "---\ntitle: T\ntags:\n  - featured\n  - old\n---\nB\n",
      { tags: ["new"] }
    );
    assert.match(out, /- "featured"/);
    assert.match(out, /- "new"/);
  });

  it("handles inline tag format", () => {
    const out = a._updateFrontMatter("---\ntitle: T\ntags: [a, b]\n---\nB\n", {
      tags: ["c"],
    });
    assert.match(out, /- "c"/);
    assert.doesNotMatch(out, /\[a, b\]/);
  });

  it("writes an empty list when tags are cleared", () => {
    const out = a._updateFrontMatter("---\ntitle: T\ntags:\n  - a\n---\nB\n", {
      tags: [],
    });
    assert.match(out, /tags: \[\]/);
  });

  it("adds a category block", () => {
    const out = a._updateFrontMatter("---\ntitle: T\n---\nB\n", {
      category: "craft",
    });
    assert.match(out, /categories:\n  - "craft"/);
  });

  it("returns content unchanged without front matter", () => {
    assert.equal(a._updateFrontMatter("No FM\n", { tags: ["x"] }), "No FM\n");
  });

  it("_updateTagsInContent delegates", () => {
    const out = a._updateTagsInContent("---\ntitle: T\n---\nB\n", ["k"]);
    assert.match(out, /- "k"/);
  });
});

describe("findAllArticles", () => {
  it("finds hierarchical article dirs", () => {
    const root = tmpdir();
    const article = path.join(root, "posts", "2026", "01", "slug");
    fs.mkdirSync(article, { recursive: true });
    fs.writeFileSync(path.join(article, "index.md"), "---\ntitle: T\n---\nB\n");
    const a = analyzer({ contentDirs: [root] });
    const found = a.findAllArticles();
    assert.equal(found.length, 1);
    assert.equal(found[0].indexPath, path.join(article, "index.md"));
  });

  it("returns empty for missing dirs", () => {
    const a = analyzer({ contentDirs: [path.join(tmpdir(), "nope")] });
    assert.deepEqual(a.findAllArticles(), []);
  });
});
