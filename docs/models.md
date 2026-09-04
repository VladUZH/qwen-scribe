# Models

Qwen Scribe transcribes with Qwen3-ASR through MLX. The picker offers four
entries: the two upstream models, downloaded from Hugging Face on first use,
and a quantized variant of each, made on your Mac from those same weights
when you ask for it. Nothing else is ever fetched; the supply-chain stance in
[SECURITY.md](../SECURITY.md) is unchanged.

| Picker entry | What it is | Memory when loaded | Where it comes from |
| --- | --- | --- | --- |
| **1.7B — accuracy** | `Qwen/Qwen3-ASR-1.7B`, fp16 | about 3.4 GB | Hugging Face, on first use |
| **1.7B 8-bit — fastest accurate** | the 1.7B quantized to 8 bits, group size 64 | about 1.9 GB | made on this Mac from the 1.7B weights |
| **0.6B — speed** | `Qwen/Qwen3-ASR-0.6B`, fp16 | about 1.2 GB | Hugging Face, on first use |
| **0.6B 4-bit — fastest** | the 0.6B quantized to 4 bits, group size 64 | about 0.5 GB | made on this Mac from the 0.6B weights |

The memory figures are what the loaded weights take in unified memory, in
addition to normal application overhead. The picker shows the measured size
on disk of a prepared variant next to its entry.

## What to expect

**Speed.** The upstream library's committed benchmarks report roughly 3x
faster decoding for the 1.7B at 8 bits and a 4.7x speed-up for the 0.6B at
4 bits, against the fp16 model on the same Mac. Real files vary with the
audio and the machine; the speed column of `compare_models.py` is the number
that matters for your recordings.

**Quality.** In the same benchmarks the 8-bit 1.7B is within noise of fp16.
Four bits is a real reduction, and the 0.6B has less to give away, so the
4-bit variant is the right choice for quick notes and dictation where speed
is worth an occasional slip, and the wrong one for names, numbers and
non-English audio you cannot check. The default model stays the fp16 1.7B;
every quantized variant is opt-in.

Measured word-difference rates on real recordings, quiet, noisy and
multilingual, are added here once the owner has run the comparison below;
until then the upstream figures are the published expectation.

## Preparing a variant

Choose it in either model picker. The note under the picker says what it
needs; **Prepare** queues the conversion in the same queue as transcriptions,
so it waits for a running file and never competes with it for the GPU. The
steps are visible in the queue: the upstream weights are downloaded first if
they are not in the cache, the fp16 model is loaded, quantized and saved,
and the entry becomes ready. Any loaded model is released before the
conversion starts, since it holds a full fp16 copy while it works; the next
job reloads what it needs in a few seconds. Cancelling leaves nothing behind.

A prepared variant lives in `~/Library/Application Support/Qwen Scribe/models`
(or `QWEN_SCRIBE_MODEL_DIR`) and survives app upgrades. **Remove** under the
picker deletes it again; preparing it once more takes the same few minutes.

From a terminal, `python quantize_8bit.py [1.7b-8bit|0.6b-4bit]` does the
same conversion into the same place. A conversion made by an older version
of that script next to `server.py` is adopted into the store when the server
starts.

Choosing a variant that is not prepared yet is allowed, so it can be
prepared from the same place; a file sent for it in the meantime is refused
with a message that says so, and a dictation with it fails the same way in
the HUD until it is ready.

## Checking a variant on your own recordings

```bash
source .venv/bin/activate
python compare_models.py meeting.m4a lecture.mp3        # every prepared variant
python compare_models.py --variant 0.6b-4bit --language English interview.wav
```

For each file it transcribes with the fp16 model and the variant, prints both
runtimes and the speed-up, and the word-level difference rate of the
quantized transcript measured against the fp16 one, with the differing spans.
Under about 1% with no changed names or numbers, the quantization is lossless
for your purposes. Test the recordings you actually care about: quiet, noisy,
and in every language you use.
