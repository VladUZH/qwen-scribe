# Third-party notices

Qwen Scribe is original application code distributed under Apache-2.0. It
downloads or installs the following principal third-party components at setup
or first transcription; they are not vendored in this repository.

| Component | Purpose | License |
| --- | --- | --- |
| [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | Speech-recognition model and weights | Apache-2.0 |
| [mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) | Qwen3-ASR implementation for Apple Silicon | Apache-2.0 |
| [MLX](https://github.com/ml-explore/mlx) | Apple Silicon machine-learning framework | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) | Local HTTP API | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn) | Local ASGI server | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Streaming form uploads | Apache-2.0 |

Each installed Python package may bring transitive dependencies under its own
license. The package metadata installed into the application's private virtual
environment is the authoritative record for those versions and licenses.

Qwen Scribe does not redistribute the Qwen3-ASR model weights. Users obtain
them from the upstream Hugging Face repository on first use and remain subject
to the upstream model license and terms.
