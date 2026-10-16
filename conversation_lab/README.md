# TinyTable conversation lab

This is a deterministic, offline test laboratory for TinyTable's identity and
transport boundaries. It does not start FastAPI, open a provider connection,
read credentials, or write to SQLite.

Run the fixtures and generate Markdown/JSON reports:

```bash
.venv/bin/python -m conversation_lab
```

Reports are written to `conversation_lab/out/`, which is ignored by Git.
Fixtures are the tracked JSON files in `conversation_lab/fixtures/`.

Use strict mode when a known risk should make the command fail:

```bash
.venv/bin/python -m conversation_lab --strict
```

The lab keeps direct Juniper/Wren forwarding and authorship boundaries
deterministic. Deferred agent-assistance fixtures are retained separately for
future work and are not part of the current Juniper/Wren deployment path.

The reports show three views:

1. Internal system metadata, including content author and source.
2. Transport identity, showing which provider session carries the text.
3. Juniper-visible text, showing what Juniper could reasonably believe.

The current acceptance policy is that provider messages remain exact copies,
human-authored messages remain attributed to Sam, and no provider
message is sent through another provider's session without explicit approval.

Reliability fixtures also cover Juniper/Wren exact forwarding, bundled provider
messages, recording failure, and record-only retry.
The retry rule is strict: a successful provider send is never repeated merely
because TinyTable's local recording step needs another attempt.
