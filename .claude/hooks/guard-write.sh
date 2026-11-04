#!/usr/bin/env bash
# PreToolUse(Write|Edit)：秘密ファイルと演者のデータへの書き込みを拒否する（演者のデータは CLI だけが書く）
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in
  */.env|*.key|*credentials*.json|*/netacho-data/*)
    jq -n --arg f "$f" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:("書き込み禁止: " + $f + "（秘密は手で扱う。演者のデータは CLI で。CLAUDE.md）")}}'
    ;;
esac
exit 0
