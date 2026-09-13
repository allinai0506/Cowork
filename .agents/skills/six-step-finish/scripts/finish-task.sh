#!/usr/bin/env bash
# Input: task branch (auto-detected), optional flags
# Output: merge confirmation (forge-optional), anchor sync, branch/worktree/lock cleanup
# Pos: universal six-step task finish — generalized from xiyu scripts/agent-finish-task.sh
#      (merge confirmation accepts gitee/github/gitlab/none; anchor branch is parameterized)
# Six steps: 1 知识沉淀  2 Wiki checkpoint  3 合并确认  4 锚点同步  5 分支核对  6 卫生检查
# 维护声明: 步骤顺序与 xiyu agent-finish-task.sh 保持同构，差异以本文件注释为准。
set -euo pipefail

# ─── 颜色 ──────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*" >&2; }
hdr()   { echo -e "${CYAN}── $* ──${NC}"; }

# ─── 参数解析 ──────────────────────────────────────────────────────────────────
DRY_RUN=0
FORCE=0
AUTO_CONFIRM=0
INCLUDE_REMOTE=0
POSITIONAL_ARGS=()
BASE_BRANCH="${FINISH_BASE_BRANCH:-}"
FORGE="${FINISH_FORGE:-auto}"
ANCHOR_MODE="${FINISH_ANCHOR_MODE:-auto}"
ANCHOR_EXPLICIT="${FINISH_ANCHOR:-}"
ANCHOR_MODE_SET=0

usage() {
  cat <<'USAGE'
Usage:
  finish-task.sh [branch-name] [options]

Six-step task finish: 知识沉淀 → wiki checkpoint → 合并确认 → 锚点同步 → 分支核对 → 卫生检查

Options:
  branch-name       要收尾的任务分支（默认：当前分支）
  --base <branch>   基准分支（默认：解析 origin/HEAD，否则 main）        [FINISH_BASE_BRANCH]
  --forge <f>       合并确认的 forge 复核: auto|gitee|github|gitlab|none  [FINISH_FORGE]
  --anchor <branch> 收尾后切回的锚点分支                                  [FINISH_ANCHOR]
  --anchor-mode <m> auto|anchor|base|keep（默认 auto）                    [FINISH_ANCHOR_MODE]
  --include-remote  同时清理远端分支
  --force           跳过合并确认，强制清理（仅用于确定已合入但检测失败）
  --dry-run         仅检查，不实际执行任何删除操作
  --yes             跳过确认提示，自动执行（未提交变更将被丢弃，慎用）
  -h, --help        显示帮助信息

Examples:
  finish-task.sh                                    # 当前分支，forge/锚点全 auto
  finish-task.sh --forge none                       # 无远端/纯 git 检查
  finish-task.sh agent/claude/fix-login --anchor agent/claude-init --include-remote
  finish-task.sh --dry-run                          # 仅打印收尾计划
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)        DRY_RUN=1;         shift ;;
    --force)          FORCE=1;           shift ;;
    --include-remote) INCLUDE_REMOTE=1;  shift ;;
    --yes)            AUTO_CONFIRM=1;    shift ;;
    --base)           BASE_BRANCH="$2";  shift 2 ;;
    --forge)          FORGE="$2";        shift 2 ;;
    --anchor)         ANCHOR_EXPLICIT="$2"; shift 2 ;;
    --anchor-mode)    ANCHOR_MODE="$2"; ANCHOR_MODE_SET=1; shift 2 ;;
    *)                POSITIONAL_ARGS+=("$1"); shift ;;
  esac
done

case "$FORGE" in auto|gitee|github|gitlab|none) ;; *) error "--forge 仅支持 auto|gitee|github|gitlab|none"; exit 1 ;; esac
case "$ANCHOR_MODE" in auto|anchor|base|keep) ;; *) error "--anchor-mode 仅支持 auto|anchor|base|keep"; exit 1 ;; esac
if [[ -n "$ANCHOR_EXPLICIT" && "$ANCHOR_MODE_SET" == "0" ]]; then
  ANCHOR_MODE="anchor"
fi
if [[ "$ANCHOR_MODE" == "anchor" && -z "$ANCHOR_EXPLICIT" ]]; then
  error "--anchor-mode anchor 需要同时提供 --anchor <branch>"
  exit 1
fi

# ─── Git 仓库检测 ──────────────────────────────────────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
  error "Not a git repository"
  exit 1
fi
cd "$REPO_ROOT"

CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"
TASK_BRANCH="${POSITIONAL_ARGS[0]:-$CURRENT_BRANCH}"
if [[ -z "$TASK_BRANCH" ]]; then
  error "无法确定任务分支（detached HEAD 且未传 branch-name）"
  exit 1
fi

# ─── 基准分支与远端 ────────────────────────────────────────────────────────────
REMOTE_NAME="origin"
HAS_REMOTE=0
if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  HAS_REMOTE=1
fi

if [[ -z "$BASE_BRANCH" ]]; then
  BASE_BRANCH="$(git symbolic-ref --short "refs/remotes/${REMOTE_NAME}/HEAD" 2>/dev/null | sed "s|^${REMOTE_NAME}/||" || true)"
fi
[[ -z "$BASE_BRANCH" ]] && BASE_BRANCH="main"

# ─── forge 识别与仓库坐标 ─────────────────────────────────────────────────────
REMOTE_URL="$(git remote get-url "$REMOTE_NAME" 2>/dev/null || true)"
REMOTE_HOST=""; REPO_SLUG=""
if [[ -n "$REMOTE_URL" ]]; then
  _u="${REMOTE_URL#ssh://}"
  _u="${_u#git@}"
  _u="${_u#https://}"
  _u="${_u#http://}"
  REMOTE_HOST="${_u%%[:/]*}"
  _path="${_u#*[:/]}"
  _path="${_path%.git}"
  _path="${_path%%#*}"
  if [[ "$_path" == */* ]]; then REPO_SLUG="$_path"; fi
fi

if [[ "$FORGE" == "auto" ]]; then
  case "$REMOTE_URL" in
    *gitee.com*)  FORGE="gitee" ;;
    *github.com*) FORGE="github" ;;
    *gitlab*)     FORGE="gitlab" ;;
    *)            FORGE="none" ;;
  esac
fi

resolve_token() {
  local forge="$1" host="$2" tok=""
  case "$forge" in
    gitee)  tok="${FINISH_GITEE_TOKEN:-${GITEE_TOKEN:-}}" ;;
    github) tok="${FINISH_GITHUB_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}" ;;
    gitlab) tok="${FINISH_GITLAB_TOKEN:-${GITLAB_TOKEN:-${GL_TOKEN:-}}}" ;;
  esac
  if [[ -z "$tok" && -n "$host" ]]; then
    tok="$(printf 'protocol=https\nhost=%s\n\n' "$host" | GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=echo git credential fill 2>/dev/null | awk '/^password=/{sub(/^password=/,""); print; exit}')"
  fi
  printf '%s' "$tok"
}

# 输出: "merged <pr>" / "open <pr> <mergeable>" / "none"
forge_pr_status() {
  local forge="$1" head_enc="$2"
  local tok resp
  case "$forge" in
    gitee)
      tok="$(resolve_token gitee gitee.com)"
      if [[ -z "$tok" || -z "$REPO_SLUG" ]]; then echo "skip"; return; fi
      resp="$(curl -sf -H "Authorization: Bearer ${tok}" \
        "https://gitee.com/api/v5/repos/${REPO_SLUG}/pulls?state=all&head=${head_enc}&base=${BASE_BRANCH}" 2>/dev/null)" || { echo "skip"; return; }
      python3 -c '
import sys, json
data = json.load(sys.stdin)
pr = data[0] if data else None
if not pr: print("none"); sys.exit()
if pr.get("merged"): print("merged", pr.get("number", ""))
elif pr.get("state") == "open": print("open", pr.get("number", ""), str(pr.get("mergeable")).lower())
else: print("none")
' <<< "$resp" 2>/dev/null || echo "skip"
      ;;
    github)
      if command -v gh >/dev/null 2>&1 && [[ -n "$REPO_SLUG" ]]; then
        local merged_count open_pr
        merged_count="$(gh pr list --repo "$REPO_SLUG" --head "$TASK_BRANCH" --state merged --json number --jq 'length' 2>/dev/null || echo 0)"
        if [[ "${merged_count:-0}" != "0" ]]; then
          local num; num="$(gh pr list --repo "$REPO_SLUG" --head "$TASK_BRANCH" --state merged --json number --jq '.[0].number' 2>/dev/null || echo '?')"
          echo "merged $num"; return
        fi
        open_pr="$(gh pr list --repo "$REPO_SLUG" --head "$TASK_BRANCH" --state open --json number --jq '.[0].number' 2>/dev/null || echo '')"
        if [[ -n "$open_pr" ]]; then echo "open $open_pr"; else echo "none"; fi
        return
      fi
      tok="$(resolve_token github github.com)"
      if [[ -z "$tok" || -z "$REPO_SLUG" ]]; then echo "skip"; return; fi
      resp="$(curl -sf -H "Authorization: Bearer ${tok}" \
        "https://api.github.com/repos/${REPO_SLUG}/pulls?state=all&head=${REPO_SLUG%%/*}:${TASK_BRANCH}&base=${BASE_BRANCH}" 2>/dev/null)" || { echo "skip"; return; }
      python3 -c '
import sys, json
data = json.load(sys.stdin)
pr = data[0] if data else None
if not pr: print("none"); sys.exit()
if pr.get("merged") or pr.get("state") == "closed" and pr.get("merged_at"): print("merged", pr.get("number", ""))
elif pr.get("state") == "open": print("open", pr.get("number", ""))
else: print("none")
' <<< "$resp" 2>/dev/null || echo "skip"
      ;;
    gitlab)
      if [[ -z "$REMOTE_HOST" || -z "$REPO_SLUG" ]]; then echo "skip"; return; fi
      local enc_slug api_host
      enc_slug="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$REPO_SLUG")"
      api_host="https://${REMOTE_HOST}"
      tok="$(resolve_token gitlab "$REMOTE_HOST")"
      if command -v glab >/dev/null 2>&1; then
        local merged_count
        merged_count="$(glab mr list --repo "$REPO_SLUG" --source-branch "$TASK_BRANCH" --state merged 2>/dev/null | grep -c '^!' || true)"
        if [[ "${merged_count:-0}" != "0" ]]; then echo "merged ?"; return; fi
      fi
      if [[ -z "$tok" ]]; then echo "skip"; return; fi
      resp="$(curl -sf -H "PRIVATE-TOKEN: ${tok}" \
        "${api_host}/api/v4/projects/${enc_slug}/merge_requests?source_branch=${TASK_BRANCH}&target_branch=${BASE_BRANCH}&state=merged" 2>/dev/null)" || { echo "skip"; return; }
      python3 -c '
import sys, json
data = json.load(sys.stdin)
print(("merged %s" % data[0]["iid"]) if data else "none")
' <<< "$resp" 2>/dev/null || echo "skip"
      ;;
    *) echo "skip" ;;
  esac
}

# ─── 分支身份核对（分支核对·前置） ─────────────────────────────────────────────
AGENT_NAME=""; TASK_NAME=""
if [[ "$TASK_BRANCH" =~ ^agent/([^/]+)/([^/]+)$ ]]; then
  AGENT_NAME="${BASH_REMATCH[1]}"
  TASK_NAME="${BASH_REMATCH[2]}"
  BRANCH_TYPE="task"
else
  BRANCH_TYPE="plain"
fi

# ─── 锚点解析（锚点同步·前置） ────────────────────────────────────────────────
ANCHOR_BRANCH=""
case "$ANCHOR_MODE" in
  anchor) ANCHOR_BRANCH="$ANCHOR_EXPLICIT" ;;
  base)   ANCHOR_BRANCH="$BASE_BRANCH" ;;
  keep)   ANCHOR_BRANCH="" ;;
  auto)
    ANCHOR_BRANCH="$BASE_BRANCH"
    if [[ "$BRANCH_TYPE" == "task" ]]; then
      local_init="agent/${AGENT_NAME}-init"
      if git show-ref --verify --quiet "refs/heads/${local_init}"; then
        ANCHOR_BRANCH="$local_init"
      fi
    fi
    ;;
esac

# ─── 分支核对：受保护 / 锚点分支拒绝 ───────────────────────────────────────────
PROTECTED_BRANCHES=("main" "master")
if [[ -n "${FINISH_PROTECTED:-}" ]]; then
  IFS=',' read -r -a _extra <<< "${FINISH_PROTECTED}"
  PROTECTED_BRANCHES+=("${_extra[@]}")
fi
for pb in "${PROTECTED_BRANCHES[@]}"; do
  if [[ "$TASK_BRANCH" == "$pb" ]]; then
    error "Cannot finish a protected branch: $TASK_BRANCH"
    exit 1
  fi
done
if [[ "$TASK_BRANCH" =~ ^agent/[^/]+-init$ ]]; then
  error "Cannot finish an anchor branch: $TASK_BRANCH"
  error "Anchor (init) branches are anchor points and must not be deleted."
  exit 1
fi
if [[ "$ANCHOR_BRANCH" == "$TASK_BRANCH" ]]; then
  error "锚点分支不能与任务分支相同: $TASK_BRANCH"
  exit 1
fi

# ─── 卫生检查·前置：非交互护栏 ────────────────────────────────────────────────
INTERACTIVE=0
if [[ -t 0 ]]; then INTERACTIVE=1; fi
confirm() {  # confirm <prompt> → 0=yes
  if [[ "$AUTO_CONFIRM" == "1" ]]; then return 0; fi
  if [[ "$INTERACTIVE" == "0" ]]; then
    warn "非交互环境且未提供 --yes，跳过需确认的操作"
    return 1
  fi
  local reply
  read -r -p "$1" reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

# ─── Banner ─────────────────────────────────────────────────────────────────────
echo
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  🧹 Six-Step Finish — 六步任务收尾                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo
echo "  分支:        $TASK_BRANCH"
[[ -n "$AGENT_NAME" ]] && echo "  Agent:       $AGENT_NAME"
[[ -n "$TASK_NAME" ]] && echo "  Task:        $TASK_NAME"
echo "  基准分支:    $BASE_BRANCH"
echo "  锚点模式:    $ANCHOR_MODE${ANCHOR_BRANCH:+ → $ANCHOR_BRANCH}"
echo "  forge:       $FORGE$([[ "$FORGE" != "none" && -n "$REPO_SLUG" ]] && echo " ($REPO_SLUG @ $REMOTE_HOST)")"
[[ "$HAS_REMOTE" == "0" ]] && warn "  无 $REMOTE_NAME 远端：合并确认降级为纯 git 检查"
[[ "$DRY_RUN" == "1" ]] && echo "  模式:        🔍 DRY RUN（仅检查，不执行删除）"
echo

# ─── Step 1: 知识沉淀（提醒；Agent 应在调用本脚本前已完成） ────────────────────
hdr "Step 1/6: 知识沉淀"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "  [DRY RUN] 确认已完成 knowledge-capture / lessons-learned 回填"
else
  if [[ "$AUTO_CONFIRM" == "1" ]]; then
    warn "自动模式：Agent 应在调用前已自行完成知识沉淀"
  else
    if confirm "是否已完成知识沉淀（knowledge-capture / lessons 回填）？(y/N) "; then
      info "知识沉淀已确认"
    else
      warn "建议先完成知识沉淀再收尾（不阻塞，仅提醒）"
    fi
  fi
fi
echo ""

# ─── Step 2: Wiki Checkpoint ────────────────────────────────────────────────────
hdr "Step 2/6: Wiki Checkpoint"
WIKI_RULES=".wiki/WIKI.md"
if [[ ! -f "$WIKI_RULES" ]]; then
  info "无 .wiki/WIKI.md，跳过 wiki checkpoint"
else
  echo "  本次任务是否产生了需要回填到 Wiki 的新知识？"
  echo "  判断标准（任一满足即'是'）："
  echo "    • 修了 ≥1 个 bug，且根因不是简单笔误"
  echo "    • 涉及跨模块逻辑变更（≥3 个文件）"
  echo "    • 产生了新的业务规则或约束"
  echo "    • 发现了之前 wiki 没记录的陷阱/边界条件"
  echo ""
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [DRY RUN] 跳过交互提示（Agent 应在调用前已自行回填 wiki）"
  elif [[ "$AUTO_CONFIRM" == "1" ]]; then
    warn "自动模式已启用，Agent 应在调用前已自行回填 wiki"
  else
    if confirm "是否已完成 Wiki 回填（或确认本次无新知识需回填）？(y/N) "; then
      info "Wiki 回填检查已确认"
    else
      warn "建议先完成 Wiki 回填再收尾任务（不阻塞，仅提醒）"
    fi
  fi
fi
echo ""

# ─── Step 3: 合并确认（三重检查 + 可选 forge 复核） ────────────────────────────
hdr "Step 3/6: 合并确认"

MERGED_INTO_MAIN=0
MERGE_CHECK_DETAILS=""

# 检查 A: git branch -r --merged（远端已合入会出现在这里）
if [[ "$HAS_REMOTE" == "1" ]] && git fetch "$REMOTE_NAME" "$BASE_BRANCH" 2>/dev/null; then
  info "已拉取最新 ${REMOTE_NAME}/${BASE_BRANCH}"
  if git branch -r --merged "${REMOTE_NAME}/${BASE_BRANCH}" 2>/dev/null | grep -qwE "${REMOTE_NAME}/${TASK_BRANCH}$"; then
    MERGED_INTO_MAIN=1
    MERGE_CHECK_DETAILS="远端分支已出现在 ${REMOTE_NAME}/${BASE_BRANCH} 已合入列表"
  fi
else
  warn "无法从 ${REMOTE_NAME} 拉取 ${BASE_BRANCH}（可能无远端）"
fi

# 检查 B: git cherry（适用于本地已 rebase/merge 的分支）
if [[ "$MERGED_INTO_MAIN" == "0" ]] && git rev-parse --verify --quiet "$TASK_BRANCH" >/dev/null 2>&1; then
  CHERRY_BASE="${REMOTE_NAME}/${BASE_BRANCH}"
  git rev-parse --verify --quiet "$CHERRY_BASE" >/dev/null 2>&1 || CHERRY_BASE="$BASE_BRANCH"
  CHERRY_OUTPUT="$(git cherry "$CHERRY_BASE" "$TASK_BRANCH" 2>/dev/null || echo "ERR")"
  if [[ "$CHERRY_OUTPUT" == "" ]]; then
    MERGED_INTO_MAIN=1
    MERGE_CHECK_DETAILS="git cherry 确认分支相对 $CHERRY_BASE 无独特提交，已完全合入"
  fi
fi

# 检查 C: forge PR 状态复核（可选；无凭据/CLI 时降级）
if [[ "$MERGED_INTO_MAIN" == "0" && "$FORGE" != "none" && "$HAS_REMOTE" == "1" ]]; then
  HEAD_ENC="${TASK_BRANCH//\//%2F}"
  PR_STATUS="$(forge_pr_status "$FORGE" "$HEAD_ENC")"
  case "$PR_STATUS" in
    skip)
      warn "forge 复核不可用（无凭据或无 CLI），跳过 PR 状态检查"
      ;;
    none)
      warn "forge 上未找到 $TASK_BRANCH → $BASE_BRANCH 的已合入 PR"
      ;;
    merged*)
      MERGED_INTO_MAIN=1
      MERGE_CHECK_DETAILS="$FORGE 确认 PR #${PR_STATUS#merged } 已合并"
      ;;
    open*)
      read -r _ PR_NUM _ <<< "$PR_STATUS"
      warn "PR #$PR_NUM 仍为 open，尚未合入 $BASE_BRANCH"
      warn "  合入前先确认无冲突并 rebase: git fetch $REMOTE_NAME && git rebase ${REMOTE_NAME}/$BASE_BRANCH"
      ;;
  esac
fi

# 最终判定
if [[ "$MERGED_INTO_MAIN" == "1" ]]; then
  info "✅ 分支已合入 $BASE_BRANCH — 安全可清理 ($MERGE_CHECK_DETAILS)"
elif [[ "$FORCE" == "1" ]]; then
  warn "⚠️ 强制模式已启用，跳过合并确认！"
  warn "   请确保已手动确认代码已合入 ${BASE_BRANCH}。"
else
  error "⛔ 分支 $TASK_BRANCH 尚未合入 ${BASE_BRANCH}！"
  error "   请先通过 PR 合入 ${BASE_BRANCH}，再执行收尾。"
  error "   如需强制清理，使用 --force 参数。"
  exit 1
fi
echo ""

# ─── 卫生检查·前置：未提交变更 ─────────────────────────────────────────────────
hdr "卫生检查: 未提交变更"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  warn "当前工作区有未提交的变更："
  git status --short
  echo
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[DRY RUN] 将提示丢弃未提交变更"
  elif [[ "$AUTO_CONFIRM" == "1" ]]; then
    git checkout -- .
    git clean -fd
    info "已丢弃未提交变更"
  else
    warn "请先提交或 stash 变更后再执行收尾。"
    warn "如需强制丢弃，请使用 --yes 参数。"
    exit 1
  fi
else
  info "工作区干净，无未提交变更"
fi
echo ""

# ─── 列出待清理资源 ────────────────────────────────────────────────────────────
hdr "待清理资源清单"

CLEANUP_LIST=()

# 本地分支
if git show-ref --verify --quiet "refs/heads/$TASK_BRANCH"; then
  CLEANUP_LIST+=("本地分支: $TASK_BRANCH")
fi

# 远端分支
REMOTE_BRANCH_EXISTS=0
if [[ "$HAS_REMOTE" == "1" ]] && git ls-remote --exit-code "$REMOTE_NAME" "refs/heads/$TASK_BRANCH" >/dev/null 2>&1; then
  REMOTE_BRANCH_EXISTS=1
  CLEANUP_LIST+=("远端分支: $REMOTE_NAME/$TASK_BRANCH")
fi

# agent-locks 文件（xiyu 约定；目录不存在则跳过）
LOCK_FILES=()
if [[ -n "$TASK_NAME" && -d ".agent-locks" ]]; then
  while IFS= read -r -d '' lf; do
    LOCK_FILES+=("$lf")
    CLEANUP_LIST+=("锁文件: ${lf#.agent-locks/}")
  done < <(find .agent-locks \( -name "${TASK_NAME}.yml" -o -name "agent-${AGENT_NAME}-${TASK_NAME}*.yml" \) -type f -print0 2>/dev/null || true)
fi

# 注册到任务分支的 worktree（通用发现：不依赖目录命名约定）
TASK_WORKTREES=()
if [[ "$HAS_REMOTE" == "1" || -n "$(git worktree list 2>/dev/null)" ]]; then
  while IFS=$'\t' read -r wt_path wt_branch; do
    [[ -z "$wt_path" ]] && continue
    [[ "$wt_path" == "$REPO_ROOT" ]] && continue    # 主仓库根不动
    [[ "$wt_branch" == "refs/heads/$TASK_BRANCH" ]] || continue
    TASK_WORKTREES+=("$wt_path")
    CLEANUP_LIST+=("Worktree: $wt_path")
  done < <(git worktree list --porcelain 2>/dev/null | awk '
    /^worktree /{p=substr($0,10)}
    /^branch /{b=substr($0,8); print p"\t"b}
  ' || true)
fi

if [[ "${#CLEANUP_LIST[@]}" -eq 0 ]]; then
  info "没有发现需要清理的资源"
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [DRY RUN] 无操作"; fi
  exit 0
fi

echo "  以下资源将被清理："
for item in "${CLEANUP_LIST[@]}"; do
  echo "    • $item"
done
echo

# ─── 用户确认 ───────────────────────────────────────────────────────────────────
hdr "确认"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "  [DRY RUN] 跳过确认，仅打印操作计划"
elif [[ "$AUTO_CONFIRM" == "1" ]]; then
  info "自动模式已启用，跳过确认"
else
  if confirm "是否确认执行清理？(y/N) "; then
    :
  else
    echo "已取消"
    exit 0
  fi
fi

# ─── Step 4: 锚点同步 ───────────────────────────────────────────────────────────
hdr "Step 4/6: 锚点同步"
if [[ "$ANCHOR_MODE" == "keep" ]]; then
  info "keep 模式：不切换分支，HEAD 留在 $(git branch --show-current 2>/dev/null || echo '?')"
elif [[ "$DRY_RUN" == "1" ]]; then
  echo "  [DRY RUN] git checkout $ANCHOR_BRANCH && git pull --rebase"
elif [[ "$CURRENT_BRANCH" != "$ANCHOR_BRANCH" ]]; then
  if ! git show-ref --verify --quiet "refs/heads/$ANCHOR_BRANCH"; then
    if [[ "$ANCHOR_MODE" == "auto" && "$BRANCH_TYPE" == "task" ]]; then
      warn "锚点分支 $ANCHOR_BRANCH 不存在，回退到基准分支 $BASE_BRANCH"
      ANCHOR_BRANCH="$BASE_BRANCH"
    elif [[ "$HAS_REMOTE" == "1" ]]; then
      git fetch "$REMOTE_NAME" 2>/dev/null || true
      git checkout -b "$ANCHOR_BRANCH" "${REMOTE_NAME}/${ANCHOR_BRANCH}" 2>/dev/null || git checkout -b "$ANCHOR_BRANCH" "$BASE_BRANCH"
      info "已基于 ${REMOTE_NAME}/${ANCHOR_BRANCH} 创建并切换到 $ANCHOR_BRANCH"
    else
      git checkout -b "$ANCHOR_BRANCH" "$BASE_BRANCH"
      info "已基于 $BASE_BRANCH 创建并切换到 $ANCHOR_BRANCH"
    fi
  fi
  if [[ "$(git branch --show-current 2>/dev/null)" != "$ANCHOR_BRANCH" ]]; then
    git checkout "$ANCHOR_BRANCH"
    info "已切换到 $ANCHOR_BRANCH"
  fi
else
  info "已在锚点分支 $ANCHOR_BRANCH"
fi
if [[ "$ANCHOR_MODE" != "keep" && "$DRY_RUN" != "1" && "$HAS_REMOTE" == "1" ]]; then
  git pull "$REMOTE_NAME" "$BASE_BRANCH" --rebase 2>/dev/null || git pull "$REMOTE_NAME" "$BASE_BRANCH" || warn "拉取最新 $BASE_BRANCH 失败（不影响收尾）"
  info "已同步最新 ${REMOTE_NAME}/${BASE_BRANCH}"
fi
echo ""

# ─── Step 5: 分支核对与清理 ─────────────────────────────────────────────────────
hdr "Step 5/6: 分支清理"

# 任务 worktree（先于分支删除：被 worktree 检出的分支无法删除）
for wt in "${TASK_WORKTREES[@]+"${TASK_WORKTREES[@]}"}"; do
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [DRY RUN] git worktree remove $wt --force"
  elif [[ "$AUTO_CONFIRM" == "1" ]]; then
    git worktree remove "$wt" --force 2>/dev/null || rm -rf "$wt"
    info "已清理 worktree: $wt"
  elif confirm "是否删除此 worktree：$wt ？(y/N) "; then
    git worktree remove "$wt" --force 2>/dev/null || rm -rf "$wt"
    info "已清理 worktree: $wt"
  else
    info "跳过 worktree 清理: $wt"
  fi
done

# 本地分支（keep 模式且任务分支即当前分支时跳过）
if git show-ref --verify --quiet "refs/heads/$TASK_BRANCH"; then
  if [[ "$ANCHOR_MODE" == "keep" && "$CURRENT_BRANCH" == "$TASK_BRANCH" ]]; then
    warn "keep 模式且任务分支为当前分支，保留本地分支: $TASK_BRANCH"
  elif [[ "$DRY_RUN" == "1" ]]; then
    echo "  [DRY RUN] git branch -D $TASK_BRANCH"
  else
    git branch -D "$TASK_BRANCH" 2>/dev/null || warn "删除本地分支失败: $TASK_BRANCH"
    info "已删除本地分支: $TASK_BRANCH"
  fi
else
  info "本地分支 $TASK_BRANCH 不存在（可能已删除）"
fi

# 远端分支
if [[ "$REMOTE_BRANCH_EXISTS" == "1" ]]; then
  if [[ "$INCLUDE_REMOTE" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "  [DRY RUN] git push $REMOTE_NAME --delete $TASK_BRANCH"
    else
      git push "$REMOTE_NAME" --delete "$TASK_BRANCH"
      info "已删除远端分支: $REMOTE_NAME/$TASK_BRANCH"
    fi
  else
    warn "远端分支存在: $REMOTE_NAME/${TASK_BRANCH}（如需删除，使用 --include-remote）"
  fi
else
  info "无远端分支需要清理"
fi
echo ""

# ─── Step 6: 卫生检查（锁文件 + 孤儿 worktree） ────────────────────────────────
hdr "Step 6/6: 卫生检查"

# 锁文件
if [[ "${#LOCK_FILES[@]}" -eq 0 ]]; then
  info "无锁文件需要清理"
else
  for lf in "${LOCK_FILES[@]+"${LOCK_FILES[@]}"}"; do
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "  [DRY RUN] 删除锁文件: $lf"
    else
      rm -f "$lf"
      info "已删除锁文件: $lf"
    fi
  done
fi

# 孤儿 worktree（prunable；主仓库根除外）
ORPHAN_PATHS=()
while IFS= read -r line; do
  case "$line" in *"prunable"*) ;; *) continue ;; esac
  p="${line%% *}"
  [[ "$p" == "$REPO_ROOT" ]] && continue
  ORPHAN_PATHS+=("$p")
done < <(git worktree list 2>/dev/null || echo "")

if [[ "${#ORPHAN_PATHS[@]}" -eq 0 ]]; then
  info "无孤儿 worktree"
else
  echo "  发现 ${#ORPHAN_PATHS[@]} 个孤儿 worktree（分支已删除，待回收）:"
  for p in "${ORPHAN_PATHS[@]}"; do
    echo "    • $p"
  done
  echo
  if [[ "$DRY_RUN" == "1" ]]; then
    for p in "${ORPHAN_PATHS[@]}"; do echo "  [DRY RUN] rm -rf $p + git worktree prune"; done
  elif [[ "$AUTO_CONFIRM" == "1" ]]; then
    for p in "${ORPHAN_PATHS[@]}"; do rm -rf "$p"; warn "已清理孤儿 worktree: $p"; done
    git worktree prune 2>/dev/null || warn "git worktree prune 失败（跳过）"
    info "已执行 git worktree prune 清理注册"
  elif confirm "是否删除这些孤儿 worktree？(y/N) "; then
    for p in "${ORPHAN_PATHS[@]}"; do rm -rf "$p"; warn "已清理孤儿 worktree: $p"; done
    git worktree prune 2>/dev/null || warn "git worktree prune 失败（跳过）"
    info "已执行 git worktree prune 清理注册"
  else
    info "跳过孤儿 worktree 清理"
  fi
fi

# ─── 完成 ───────────────────────────────────────────────────────────────────────
echo
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ 任务收尾完成                                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo
echo "  当前分支: $(git branch --show-current 2>/dev/null || echo '?')"
echo "  最新提交: $(git log -1 --oneline 2>/dev/null || echo '?')"
echo
