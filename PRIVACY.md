# Privacy

Qwen Scribe is designed as a local application. Audio, video, transcript text,
and desktop dictation are processed on the Mac running the application. There
is no Qwen Scribe cloud service, account, analytics SDK, advertising SDK, or API
key.

## Data stored on the Mac

- Completed transcripts are automatically stored as readable, unencrypted JSON
  in `~/Library/Application Support/Qwen Scribe/transcripts`. That is the
  default location; `QWEN_SCRIBE_DATA_DIR` overrides it, so check that variable
  before assuming where transcripts are on a given install.
- Preferences are stored in `~/Library/Application Support/Qwen Scribe/settings.json`:
  the dictation push-to-talk key, model, language, and whether dictations are
  saved to history; the dictation dictionary, which is the names and terms you
  typed in yourself and which is sent to the model as a vocabulary hint; and
  the file-transcription options, including the domain vocabulary. They
  contain no audio, recordings, or transcripts.
- The app's Python runtime lives in
  `~/Library/Application Support/Qwen Scribe/runtime`.
- Diagnostic output is written to `~/Library/Logs/QwenScribe.log` and may
  contain media filenames, local paths, dependency output, and error messages.
- Downloaded model weights normally live in the Hugging Face cache, usually
  `~/.cache/huggingface/hub`.
- Uploaded media is copied to a randomly named temporary file while a job runs
  and is deleted when that job succeeds or is cancelled. The copy for a job
  that failed is kept for up to an hour so that Retry does not need the file
  uploaded again, then deleted. Every staged copy is deleted when the server
  stops, whether or not its job had finished. Files left by a hard crash are
  removed after they are more than 24 hours old on a later server start.
- Desktop dictation is recorded into a temporary WAV file and deleted after
  transcription or failure. Its completed transcript is saved in history like
  any other completed job, unless **Save dictations to history** is switched
  off in the dictation settings. Then the text is held in memory only until
  the helper has collected and pasted it, at most a minute, and is never
  written to disk.

Transcript files are not encrypted by Qwen Scribe. macOS permissions, disk
encryption, backups, and other local accounts determine who else can read them.
Deletion uses normal filesystem deletion, not cryptographic secure erasure.

## Network activity

An internet connection is needed to install pinned Python dependencies from
PyPI and to download a selected model from Hugging Face the first time it is
used. These requests do not contain the user's audio or transcript. The app
sets the standard Hugging Face telemetry opt-out environment variables and the
web interface uses system fonts rather than remote font services.

After dependencies and model weights are present, normal transcription and
dictation can run without a network connection.

## macOS permissions

Desktop dictation is optional and requests:

- **Microphone:** records only while the configured push-to-talk key is held.
- **Input Monitoring:** the native helper subscribes to modifier-change events
  and reacts only to the configured right-side modifier key: Command, Option,
  or Control. It does not implement a text key logger.
- **Accessibility:** sends Command-V to the application that was focused when
  dictation began.

To paste without permanently replacing the clipboard, the helper temporarily
copies the current pasteboard items into process memory, inserts the transcript,
then restores the prior items if no other app changed the clipboard meanwhile.
The snapshot is not written to disk or sent over the network.

When Accessibility access is missing, or the app that was focused has quit, the
helper cannot insert text. It then leaves the transcript on the clipboard and
shows the failure HUD rather than reporting a successful insertion, so the
clipboard does keep the transcript in that case until something else replaces
it.

## Local API protection

The server binds to `127.0.0.1` by default. It rejects untrusted Host headers
and cross-origin browser requests. It has no user authentication because it is
intended only for the same logged-in user on the same Mac. Do not expose the
server on a LAN or the public internet.

## Removing data

Use **Delete** or **Delete all** in the app to remove transcript history. To
remove everything, stop Qwen Scribe, delete the two Application Support and log
paths above, and remove its app bundle. The Hugging Face cache can be removed
separately, but it may be shared with other local machine-learning applications.
