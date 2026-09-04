"""Qwen Scribe: local transcription and dictation server for Apple Silicon.

The package is split by what each part owns:

- ``config``: paths, limits, and the tables the interface is built from
- ``settings``: the persisted, validated settings the browser and the native
  helper both follow
- ``sessions``: the cached model sessions
- ``jobs``: the job store, the single worker, and cancel/retry/shutdown
- ``history``: transcript files, search, and export
- ``dictation``: the native helper's heartbeat and permission state
- ``api``: the FastAPI application that ties them together

``server.py`` at the repository root is the entry point that runs ``api.app``.
"""
