// Runs under Node's built-in runner: node --test tests/js/transcript.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { sentenceLines, buildSrt, srtTime, hasSentenceSplitter } = require("../../static/transcript.js");

test("the splitter is available on this engine", () => {
  assert.equal(hasSentenceSplitter(), true);
});

test("splits after sentence-final punctuation followed by a capital", () => {
  assert.equal(
    sentenceLines("First one. Second one! Third one? Fourth."),
    "First one.\nSecond one!\nThird one?\nFourth.",
  );
});

test("keeps initials, titles, abbreviations and decimals on one line", () => {
  const text = "Dr. Smith met J. R. R. Tolkien at 3.5 p.m. Is that right? It costs 2.50 e.g. daily. Fine.";
  assert.equal(
    sentenceLines(text),
    "Dr. Smith met J. R. R. Tolkien at 3.5 p.m. Is that right?\nIt costs 2.50 e.g. daily.\nFine.",
  );
});

test("does not split before a lowercase continuation", () => {
  assert.equal(sentenceLines("See e.g. this one. and that one. Then this."), "See e.g. this one. and that one.\nThen this.");
});

test("keeps a closing quote or bracket with the sentence it ends", () => {
  assert.equal(sentenceLines('He said "the end." Next sentence.'), 'He said "the end."\nNext sentence.');
  assert.equal(sentenceLines("Done (finally). Next."), "Done (finally).\nNext.");
});

test("splits Chinese and Japanese at ideographic stops without needing spaces", () => {
  assert.equal(sentenceLines("第一句。第二句！第三句？"), "第一句。\n第二句！\n第三句？");
  assert.equal(sentenceLines("他说“好。”然后走了。"), "他说“好。”\n然后走了。");
});

test("leaves existing line breaks inside their sentence", () => {
  assert.equal(sentenceLines("Line one\ncontinues. Next."), "Line one\ncontinues.\nNext.");
});

test("empty and missing text stay empty", () => {
  assert.equal(sentenceLines(""), "");
  assert.equal(sentenceLines(undefined), "");
});

test("srt timestamps are hh:mm:ss,mmm", () => {
  assert.equal(srtTime(0), "00:00:00,000");
  assert.equal(srtTime(61.5), "00:01:01,500");
  assert.equal(srtTime(3600 + 59.999), "01:00:59,999");
});

test("cues are numbered, timed, and joined with spaces", () => {
  const words = [
    { text: "Hello", start: 0.0, end: 0.4 },
    { text: "there", start: 0.5, end: 0.9 },
    { text: "'s", start: 0.9, end: 1.0 },
  ];
  assert.equal(buildSrt(words, "English"), "1\n00:00:00,000 --> 00:00:01,000\nHello there's\n");
});

test("a pause longer than 0.9 s starts a new cue", () => {
  const words = [
    { text: "One", start: 0.0, end: 0.3 },
    { text: "Two", start: 2.0, end: 2.3 },
  ];
  assert.equal(buildSrt(words, "English").split("\n\n").length, 2);
});

test("a cue never exceeds six seconds or forty-two characters", () => {
  const long = Array.from({ length: 20 }, (_, i) => ({ text: "word" + i, start: i * 0.5, end: i * 0.5 + 0.4 }));
  const cues = buildSrt(long, "English").trim().split("\n\n");
  for (const cue of cues) {
    const [, timing, text] = cue.split("\n");
    const [start, end] = timing.split(" --> ").map(t => {
      const [h, m, rest] = t.split(":"); const [s, ms] = rest.split(",");
      return (+h) * 3600 + (+m) * 60 + (+s) + (+ms) / 1000;
    });
    assert.ok(end - start <= 6.01, `cue too long: ${timing}`);
    assert.ok(text.length <= 42 + 7, `cue text too long: ${text}`);
  }
  assert.ok(cues.length >= 3);
});

test("unspaced scripts are joined without spaces and capped at twenty characters", () => {
  const chars = Array.from("今天天气很好我们去公园散步吧然后吃饭好不好呀", (c, i) => ({ text: c, start: i * 0.2, end: i * 0.2 + 0.15 }));
  const srt = buildSrt(chars, "Chinese");
  const texts = srt.trim().split("\n\n").map(c => c.split("\n")[2]);
  assert.ok(texts.every(t => !t.includes(" ")));
  assert.ok(texts.every(t => t.length <= 20));
  assert.equal(texts.join(""), "今天天气很好我们去公园散步吧然后吃饭好不好呀");
});

test("no words produce an empty subtitle file", () => {
  assert.equal(buildSrt([], "English"), "");
  assert.equal(buildSrt(undefined, "English"), "");
});
