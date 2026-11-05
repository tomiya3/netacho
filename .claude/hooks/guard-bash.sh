#!/usr/bin/env bash
# PreToolUse(Bash)：
#  1. PR の作成は、そのブランチで文脈なしレビュー（/pr の手順 2）を呼んだ印が無ければ止める（deny）
#  2. main への push は止める（deny。settings.json の deny は先頭一致なので HEAD:main などを拾えない）
#  3. 消す・書き換える・マージする系のコマンドは、ユーザーの承認（ask）を挟む
# 判定はコマンド文字列の正規表現。コマンドの位置（行頭、; & | ( $( then do else の後、sudo/env/xargs などの後、
# /bin/ などのパスや \ の後）に来たものだけを拾う。文中の語では止めにくいが、引用符の中の ; | の後や、
# 複数行の本文の行頭に来た語は ask になる（安全側。PR 本文は --body-file で渡すと減る）。
# 防ぎきれないもの（既知の穴）：バッククォートのコマンド置換（Markdown の `…` と区別できないので見ない）、変数やエイリアス経由、python -c や bash script.sh から呼ぶ外部コマンド。bash -c と eval は一律 ask
if ! command -v jq >/dev/null; then  # jq が無いと判定できない。黙って通さない
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"jq が無いのでコマンドを判定できない（.claude/hooks/guard-bash.sh）"}}'
  exit 0
fi
input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
[ -n "$cmd" ] || exit 0
cwd=$(jq -r '.cwd // empty' <<<"$input")
cwd=${cwd:-$PWD}

AT='(^|[;&|(]|\$\(|(^|[ ;])(then|do|else) ) *([A-Za-z_][A-Za-z0-9_]*=[^ ]* +)*((sudo|env|command|nohup|time|xargs|exec)( +-[^ ]+| +[A-Za-z_][A-Za-z0-9_]*=[^ ]*)* +)*([^ ;&|]*/)?\\?'
GIT='git( +-[Cc] +[^ ]+| +--[a-z-]+(=[^ ]+)?)*'
GH='gh( +(-R|--repo) +[^ ]+)?'
N='[^;&|]*'  # 同じコマンドの中だけ（; && | をまたがない）

decide() {  # $1 = deny|ask, $2 = 理由
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
  exit 0
}
has() { grep -qE -- "${AT}$1" <<<"$cmd"; }

# rm -r / -f を承認なしで通す場所：Claude の一時フォルダ（/tmp/claude-*、/private/tmp/claude-*）と、
# この repo の out/ の下（git に入らない生成物）。out/ そのもの・out/demo/（大文字小文字を問わない）・
# out/ の直下へのワイルドカードは除く。
# 通すのは、コマンド全体が rm だけ（; と && でつないだもの）で、全部のパスがその場所のときだけ。
# cd・サブシェル・{ }・! などが混ざる、引用符・$・~・`・\・{・..・/./・// を含む、シンボリックリンクを通る、
# といった判定しきれない形は、全部承認制に倒す
rm_ok() {
  local root proj seg w tmp rel low comp p words paths=0
  [[ "$cmd" =~ [\"\'\$~\`\\{}()!\<\>] || "$cmd" == *..* || "$cmd" == */./* || "$cmd" == *//* ]] && return 1
  [[ "$cmd" == *"||"* || "$cmd" =~ (^|[^&])\&([^&]|$) ]] && return 1  # || とバックグラウンドの & は扱わない
  root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || root=
  proj=${CLAUDE_PROJECT_DIR:-}
  if [ -n "$proj" ] && [ -n "$root" ]; then  # 別の repo の out/ は対象外（worktree は同じ repo とみなす）
    [ "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = \
      "$(git -C "$proj" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ] || root=
  fi
  # macOS の /tmp は /private/tmp へのリンクで、git の root も /private/tmp で始まる。そのときだけ揃える
  tmp=$(cd -P /tmp 2>/dev/null && pwd)
  [ "$tmp" = /private/tmp ] || tmp=/tmp
  while IFS= read -r seg; do
    read -ra words <<<"$seg"
    [ "${#words[@]}" -gt 0 ] || continue
    [[ "${words[0]}" =~ ^(/bin/|/usr/bin/)?rm$ ]] || return 1  # rm 以外のコマンドが混ざったら判定しない
    for w in "${words[@]:1}"; do
      [[ "$w" == -* ]] && continue
      paths=$((paths + 1))
      [[ "$w" =~ [*?\[][^/]*/ ]] && return 1                     # ワイルドカードは最後の成分だけ（リンクを辿らない）
      [[ "$w" =~ (^|/)\.[^/]*[*?\[] || "$w" =~ (^|/)\.$ ]] && return 1  # .* や末尾の . は扱わない
      # macOS の /tmp は /private/tmp へのリンクで、git の root も /private/tmp で始まる。そのときだけ揃える
      tmp=/tmp
      if [ -L /tmp ] && [ "$(cd -P /tmp 2>/dev/null && pwd)" = /private/tmp ]; then
        tmp=/private/tmp
        w=${w/#\/tmp\//\/private\/tmp\/}
      fi
      if [[ "$w" == "$tmp"/claude-* && "${w#"$tmp"/}" =~ ^claude-[^/*?\[]+/. ]]; then
        p=""
      else
        [ -n "$root" ] || return 1
        if [[ "$w" == /* ]]; then
          [[ "$w" == "$root"/* ]] || return 1
          rel=${w#"$root"/}
        else
          rel=${cwd#"$root"}; rel=${rel#/}; rel=${rel:+$rel/}${w#./}
        fi
        low=$(tr '[:upper:]' '[:lower:]' <<<"$rel")
        [[ "$low" =~ ^out/[^/*?\[]+(/|$) ]] || return 1
        [[ "$low" =~ ^out/demo(/|$) ]] && return 1
        p=$root
      fi
      # 途中にシンボリックリンクがあれば通さない（リンク先を消すため）。read で分けるのでグロブは展開しない
      local comps
      if [ -n "$p" ]; then IFS=/ read -ra comps <<<"$rel"; else IFS=/ read -ra comps <<<"${w#/}"; fi
      for comp in "${comps[@]}"; do
        [ -n "$comp" ] || continue
        p=$p/$comp
        [ -L "$p" ] && return 1
      done
    done
  done < <(sed -E 's/(&&|;)/\n/g' <<<"$cmd")
  [ "$paths" -gt 0 ]
}

if has "$GH +pr +create"; then
  branch=$(git branch --show-current 2>/dev/null)
  marker="$(git rev-parse --git-common-dir 2>/dev/null)/netacho-reviewed/${branch//\//_}"
  sha=$(cut -d' ' -f2 "$marker" 2>/dev/null)
  if [ -z "$sha" ] || ! git merge-base --is-ancestor "$sha" HEAD 2>/dev/null; then
    decide deny "PR の前に文脈なしレビューが要る（.claude/skills/pr の手順 2）。Agent ツールで subagent_type: reviewer を呼ぶと、このブランチに印が付く（.claude/hooks/on-reviewer.sh）"
  fi
fi

if has "$GIT +push$N ([^ ]*:)?(refs/heads/)?(main|master)( |$)" ||
   { [ "$(git branch --show-current 2>/dev/null)" = main ] && has "$GIT +push"; }; then
  decide deny "main への push は禁止（PR を通す。CLAUDE.md）"
fi

# 承認が要るもの：パターン<TAB>理由。@GIT@ @GH@ @N@ は上の定義に置き換える
n=0
while IFS= read -r line; do
  [ -n "$line" ] || continue
  [[ "$line" == *$'\t'* ]] || decide ask "guard-bash.sh の一覧が壊れている（タブが無い行：$line）"
  n=$((n + 1))
  pat=${line%%$'\t'*} why=${line#*$'\t'}
  [[ "$pat" == rm@N@* ]] && rm_ok && continue  # 一時フォルダと out/ の下の rm -r / -f は承認なし
  # 置換先は引用符で囲む（bash 5.2 以降は、囲まないと & が「一致した文字列」になる。patsub_replacement）
  pat=${pat//@GIT@/"$GIT"} pat=${pat//@GH@/"$GH"} pat=${pat//@N@/"$N"}
  has "$pat" && decide ask "承認が要るコマンド：${why}（CLAUDE.md「承認を得てから実行するコマンド」）"
done <<'EOF'
@GIT@ +push@N@( --delete| --mirror| --force| -[a-zA-Z]*[df][a-zA-Z]*( |$)| :[^ ]| \+[^ ])	リモートのブランチを消す・履歴を上書きする push
@GIT@ +branch@N@( -[a-zA-Z]*[dD][a-zA-Z]*( |$)| --delete)	ブランチを消す
@GIT@ +reset@N@ --hard	作業中の変更を捨てる
@GIT@ +clean@N@( -[a-zA-Z]*f| --force)	追跡していないファイルを消す
@GIT@ +(checkout|restore|switch)@N@( \.( |$)| -f( |$)| --force| --discard-changes)	作業中の変更をまとめて捨てる
@GIT@ +(rebase|filter-branch|filter-repo|replace)( |$)	履歴を書き換える
@GIT@ +(update-ref +-d|reflog +(expire|delete)|gc +--prune|prune)	参照・履歴を消す
@GIT@ +stash +(drop|clear)	stash を消す
@GIT@ +tag@N@( -d| --delete)	タグを消す
@GIT@ +rm@N@ -[a-zA-Z]*r	ファイルを再帰的に消す
@GIT@ +worktree +remove@N@( -f| --force)	変更の残る worktree を消す
rm@N@ (-[a-zA-Z]*[rRf]|--recursive|--force)	ファイルを再帰的・強制的に消す
rmdir 	ディレクトリを消す
find@N@ (-delete|-exec +rm)	ファイルを消す
((bash|sh|zsh) +-c|eval) 	中身を判定できない（bash -c、eval）
@GH@ +pr +(merge|close)	PR をマージする・閉じる（マージはユーザーが言ったときだけ）
@GH@ +issue +(delete|transfer)	Issue を消す・移す（閉じるのは承認なしで可）
@GH@ +repo +(delete|edit|rename|archive)	repo の設定を変える・消す
@GH@ +(release|secret|variable) +(delete|remove)	GitHub 上のものを消す
@GH@ +api@N@ (-X|--method)[ =]*(DELETE|PUT|PATCH|POST|delete|put|patch|post)	GitHub API で作る・消す・書き換える
@GH@ +api@N@ (-f|-F|--field|--raw-field|--input)[ =]	GitHub API で作る・書き換える（-f は POST になる）
@GH@ +api +graphql	GitHub API（graphql）で書き換えうる
EOF
[ "$n" -gt 0 ] || decide ask "guard-bash.sh の一覧が空"
exit 0
