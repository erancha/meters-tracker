# Tools

Hands-on tools, run from the repo root. The ones that work on a deployed stack use the stack the
git branch selects. Each prints its full usage with `--help`.

- `read-meter.sh <image>` — reads one photo through the real model and prints the result.
- `eval-intents.sh` — classifies the recorded messages of `tests/intents.json` with the real
  model and reports each intent that differs; billed.
- `eval-readings.sh` — scores a model on the photos the deployed model failed to read; billed.
- `replay.sh <email> …` — replays the owner's story on a deployed stack with the real model, as
  a smoke test; resets that account first; billed.
- `reset-account.sh <email>` — deletes everything one account owns in a deployed stack.
- `backup-restore.sh backup | restore <folder> | --cron` — backs a deployed stack's data up to a
  local folder and restores it.

Back to the [README](../../README.md).
