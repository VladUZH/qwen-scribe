// Transcript text helpers: the sentence-per-line view and the SRT builder.
//
// Loaded by index.html as a plain script (it sets window.QwenScribeTranscript)
// and by tests/js under Node through require(). No dependencies, no build
// step, in line with CONTRIBUTING.md.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.QwenScribeTranscript = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Split after sentence-final punctuation, keeping the mark — and any closing
  // quote or bracket belonging to it — on the sentence it ends. Two cases,
  // because Chinese and Japanese are written without spaces: an ideographic
  // full stop is a boundary even when the next character follows it
  // immediately, whereas an ASCII period also needs the following whitespace
  // to tell "end." from "3.5". Requiring the next sentence not to start
  // lowercase is what keeps "e.g. this" on one line; Qwen3-ASR's punctuation
  // and capitalization are what make that reliable.
  // Guards, in order: not right after an initial or a dotted abbreviation
  // ("J. R. R.", "U.S.", "e.g.", "i.e."), and not after a common title. The
  // same guards also hold back "p.m. Then" — the price of never breaking a
  // line mid-abbreviation, which is the more visible mistake.
  // Assembled with RegExp rather than written as a literal because a literal
  // is parsed with the script: on an engine without lookbehind the whole
  // interface would die rather than just this one toggle.
  // The ideographic alternative must look ahead past a closing mark, not at
  // any non-space: `\S` lets the closer group backtrack to empty and match
  // before the quote instead of after it, stranding a lone ” on its own line —
  // the exact case the group was added for.
  const CLOSERS = String.raw`["'”’»)\]}」』】）]*`;
  const NOT_CLOSER = String.raw`[^\s"'”’»)\]}」』】）]`;
  let SENTENCE_BREAK = null;
  try {
    SENTENCE_BREAK = new RegExp(
      String.raw`(?<=[。！？])(${CLOSERS})(?=${NOT_CLOSER})` +
      String.raw`|(?<=[.!?。！？…])(?<!\b[A-Za-z]\.)` +
      String.raw`(?<!\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|No|Fig|Inc|Ltd|Co)\.)` +
      String.raw`(${CLOSERS})\s+(?=[^a-z])`,
      "g",
    );
  } catch {
    SENTENCE_BREAK = null;
  }

  function sentenceLines(text) {
    if (!SENTENCE_BREAK) return text || "";
    // Split on a marker of our own rather than on "\n", so a line break the
    // transcript already contained is left inside its sentence instead of
    // being treated as another boundary.
    return (text || "")
      .replace(SENTENCE_BREAK, "$1$2\u0000")
      .split("\u0000")
      .map(sentence => sentence.trim())
      .filter(Boolean)
      .join("\n");
  }

  // Scripts written without spaces get one segment per character, so joining
  // with spaces would put a gap between every character.
  const CJK_LANGUAGES = new Set([
    "chinese", "zh", "zh-cn", "zh-tw", "cantonese", "yue",
    "japanese", "ja", "jp", "korean", "ko", "kr", "thai", "th",
  ]);

  const srtTime = seconds => {
    const ms = Math.round(seconds * 1000);
    const pad = (n, width = 2) => String(n).padStart(width, "0");
    return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
  };

  // Build SRT cues from word-level segments: break on long pauses, ~42
  // characters (20 for unspaced scripts), or 6 s per cue.
  function buildSrt(words, language) {
    const unspaced = CJK_LANGUAGES.has(String(language || "").toLowerCase());
    const maxChars = unspaced ? 20 : 42;
    const cues = [];
    let cue = null;
    for (const w of words || []) {
      const startNew = !cue
        || (w.start - cue.end) > 0.9
        || (cue.text.length + w.text.length) > maxChars
        || (w.end - cue.start) > 6;
      if (startNew) {
        if (cue) cues.push(cue);
        cue = { start: w.start, end: w.end, text: w.text };
      } else {
        cue.end = w.end;
        cue.text += (unspaced || w.text.startsWith("'") ? "" : " ") + w.text;
      }
    }
    if (cue) cues.push(cue);
    return cues.map((c, i) => `${i + 1}\n${srtTime(c.start)} --> ${srtTime(c.end)}\n${c.text.trim()}\n`).join("\n");
  }

  return {
    sentenceLines,
    buildSrt,
    srtTime,
    CJK_LANGUAGES,
    hasSentenceSplitter: () => SENTENCE_BREAK !== null,
  };
});
