"""
Qwen Scribe — local transcription server for Apple Silicon.

FastAPI wrapper around mlx-qwen3-asr (Qwen3-ASR running natively on Metal).
Upload audio or video in the browser at http://localhost:8990 — nothing
leaves your machine.

This file is the entry point; the application lives in the qwen_scribe
package next to it.
"""

from __future__ import annotations

import uvicorn

from qwen_scribe import config, jobs
from qwen_scribe.api import app  # noqa: F401  (re-exported for `uvicorn server:app`)


def main() -> None:
    # Loading the dictation model at start and releasing idle models are for
    # the real server only; the test suite never wants a model it did not ask
    # for, which is why the package leaves this off.
    jobs.background_loading = True
    print(f"\n  Qwen Scribe — open http://{config.HOST}:{config.PORT} in your browser\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
