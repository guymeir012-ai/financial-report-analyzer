#!/bin/bash
# Appends one line per agent action to the project's log note in Obsidian.
# Runs as a PostToolUse hook. Does nothing if the vault is not on this machine.
VAULT="${OBSIDIAN_VAULT:-$HOME/Second Brain}"
LOG="$VAULT/wiki/מנתח-דוחות-כספיים-יומן.md"
[ -f "$LOG" ] || exit 0

INPUT=$(cat)
TOOL=$(jq -r '.tool_name // empty' <<<"$INPUT" 2>/dev/null)
[ -n "$TOOL" ] || exit 0
case "$TOOL" in
  Bash)       DETAIL=$(jq -r '.tool_input.description // "command"' <<<"$INPUT") ;;
  Write|Edit) DETAIL=$(jq -r '.tool_input.file_path // empty' <<<"$INPUT" | sed "s|^$CLAUDE_PROJECT_DIR/||") ;;
  *)          DETAIL="" ;;
esac
DETAIL=$(printf '%s' "$DETAIL" | tr '\n|' ' /' | cut -c1-120)

TODAY=$(date +%F)
grep -q "^## $TODAY" "$LOG" || printf '\n## %s\n' "$TODAY" >> "$LOG"
printf -- '- %s | %s | %s\n' "$(date +%H:%M)" "$TOOL" "$DETAIL" >> "$LOG"
exit 0
