#!/usr/bin/env bash
# =============================================================================
# install.sh — Agent Skills のインストール支援スクリプト（汎用）
#
# リポジトリ直下1階層から SKILL.md を持つディレクトリをすべて検出し、
# スキルごとに確認しながら、指定したツール・スコープの配置先へ
# クリーンインストールする（既定: Claude Code・ユーザー = ~/.claude/skills）。
#
# Usage:
#   bash scripts/install.sh                         # ~/.claude/skills へ（スキルごとに確認）
#   bash scripts/install.sh -y                      # 確認なしでコピー（既存は削除して上書き）
#   bash scripts/install.sh --target copilot        # ~/.copilot/skills へ
#   bash scripts/install.sh --target all --scope project --project-dir ~/work/app
#   bash scripts/install.sh --dir /path/to/skills   # 任意の配置先へ
#   bash scripts/install.sh --uninstall --target all
#   bash scripts/install.sh --list-targets          # 配置先の一覧を表示
#   bash scripts/install.sh --zip                   # Claude.ai 用 ZIP を dist/ に生成
#   bash scripts/install.sh --help
#
# Environment variables:
#   CLAUDE_SKILLS_DIR  - Claude Code・ユーザースコープの配置先を上書き
#                        （デフォルト: ~/.claude/skills。従来互換）
#
# macOS 標準の bash 3.2 で動くように書いている（連想配列・mapfile を使わない）。
# =============================================================================
set -euo pipefail

# ─── 設定 ─────────────────────────────────────────────────────────────────────
ASSUME_YES=0   # -y / --yes で 1 になる
DRY_RUN=0      # --dry-run で 1 になる
ALL_TARGETS="claude copilot agents antigravity"

# ─── 色定義（TTY 時のみ有効） ─────────────────────────────────────────────────
if [ -t 1 ]; then
  C_INFO='\033[0;34m' C_WARN='\033[0;33m' C_ERR='\033[0;31m'
  C_OK='\033[0;32m'   C_BOLD='\033[1m'    C_RESET='\033[0m'
else
  C_INFO='' C_WARN='' C_ERR='' C_OK='' C_BOLD='' C_RESET=''
fi

# ─── ログヘルパー ─────────────────────────────────────────────────────────────
info()    { printf "${C_INFO}${C_BOLD}[INFO]${C_RESET} %s\n"    "$*"; }
warn()    { printf "${C_WARN}${C_BOLD}[WARN]${C_RESET} %s\n"    "$*"; }
error()   { printf "${C_ERR}${C_BOLD}[ERROR]${C_RESET} %s\n"    "$*" >&2; }
success() { printf "${C_OK}${C_BOLD}[OK]${C_RESET} %s\n"        "$*"; }
dry()     { printf "${C_BOLD}[DRY-RUN]${C_RESET} %s\n"          "$*"; }

# ─── 確認プロンプト（y/Y のときのみ 0 を返す） ────────────────────────────────
confirm() {
  local answer
  printf "%b[CONFIRM]%b %s [y/N] " "${C_BOLD}" "${C_RESET}" "$1"
  read -r answer
  [[ "${answer}" =~ ^[Yy]$ ]]
}

# ─── ヘルプ ───────────────────────────────────────────────────────────────────
usage() {
  cat <<'EOF'
install.sh — Agent Skills のインストール支援

リポジトリ直下から SKILL.md を持つディレクトリをすべて検出し、
スキルごとに確認しながらインストール / アンインストール / ZIP 生成する。

Usage:
  bash scripts/install.sh [options]

Options:
  --target, -t <list>  配置先のツール。カンマ区切りで複数指定可（既定: claude）
                         claude | copilot | agents | antigravity | all
  --scope <scope>      user（既定）| project
                         user    : ホームディレクトリ配下（全プロジェクトで使う）
                         project : ワークスペース（リポジトリ）配下
  --project-dir <path> --scope project の対象（既定: 実行時のカレントディレクトリ）
  --dir <path>         任意の配置先を直接指定（--target / --scope は無視される）
  --uninstall          インストールの代わりに、配置先からスキルを削除する
  --dry-run            何も書き込まず、行う操作だけを表示する
  --list-targets       ツール × スコープの配置先一覧を表示して終了する
  --zip                dist/ 配下にスキルごとの配布用 ZIP を生成（Claude.ai 用）
  -y, --yes            確認なしで実行（既存は削除して上書き）
  -h, --help           このヘルプを表示

Examples:
  bash scripts/install.sh                          ~/.claude/skills へコピー
  bash scripts/install.sh --target copilot -y      ~/.copilot/skills へコピー
  cd ~/work/app && bash /path/to/scripts/install.sh --scope project --target claude,copilot
  bash scripts/install.sh --target all --dry-run   コピー先だけ確認する
  bash scripts/install.sh --uninstall --target all 4 つの配置先から削除する

Environment:
  CLAUDE_SKILLS_DIR   Claude Code・ユーザースコープの配置先を上書き
                      （デフォルト: ~/.claude/skills）

Claude.ai（Web）はファイルを置く方式ではない。--zip で作った ZIP を
「カスタマイズ > スキル」に登録する。
EOF
}

# ─── ツール × スコープ → 配置先ディレクトリ ───────────────────────────────────
# 引数: target scope project_dir   出力: 配置先（末尾スラッシュなし）
# 未知の target は 1 を返す。
dest_for() {
  local target="$1" scope="$2" project_dir="$3"
  case "${target}:${scope}" in
    claude:user)         printf '%s\n' "${CLAUDE_SKILLS_DIR:-${HOME}/.claude/skills}" ;;
    claude:project)      printf '%s\n' "${project_dir}/.claude/skills" ;;
    copilot:user)        printf '%s\n' "${HOME}/.copilot/skills" ;;
    copilot:project)     printf '%s\n' "${project_dir}/.github/skills" ;;
    agents:user)         printf '%s\n' "${HOME}/.agents/skills" ;;
    agents:project)      printf '%s\n' "${project_dir}/.agents/skills" ;;
    antigravity:user)    printf '%s\n' "${HOME}/.gemini/config/skills" ;;
    antigravity:project) printf '%s\n' "${project_dir}/.agents/skills" ;;
    *) return 1 ;;
  esac
}

# ─── 配置先一覧を表示（--list-targets） ───────────────────────────────────────
list_targets() {
  local project_dir="$1" t
  printf '%-12s %-8s %s\n' "TARGET" "SCOPE" "DESTINATION"
  for t in ${ALL_TARGETS}; do
    printf '%-12s %-8s %s\n' "${t}" "user"    "$(dest_for "${t}" user "${project_dir}")"
    printf '%-12s %-8s %s\n' "${t}" "project" "$(dest_for "${t}" project "<project-dir>")"
  done
  echo ""
  echo "claude.ai    —        ファイル配置ではなく ZIP 登録（--zip で生成）"
  echo "copilot は .claude/skills・.agents/skills・~/.agents/skills も読み込む。"
  echo "antigravity の project と agents の project は同じ場所（.agents/skills）。"
}

# ─── リポジトリルート（scripts/ の1つ上）を解決 ───────────────────────────────
resolve_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && cd .. && pwd
}

# ─── SKILL.md を持つディレクトリを直下1階層からすべて検出（1行1ディレクトリ） ──
detect_skill_dirs() {
  local root="$1" d
  for d in "${root}"/*/; do
    if [[ -f "${d}SKILL.md" ]]; then
      printf '%s\n' "${d%/}"
    fi
  done
}

# ─── スキルディレクトリのファイル一覧を表示（ルート相対） ──────────────────────
list_files() {
  local skill_dir="$1" root="$2"
  find "${skill_dir}" -type f | sort | sed "s|${root}/||"
}

# ─── 配布用 ZIP を dist/<name>-<timestamp>.zip に生成 ─────────────────────────
build_zip() {
  local root="$1" name="$2" timestamp="$3"
  local dist_dir="${root}/dist"
  local zip_path="${dist_dir}/${name}-${timestamp}.zip"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    dry "ZIP を生成: ${zip_path}"
    return 0
  fi
  mkdir -p "${dist_dir}"
  # ZIP はリポジトリルートから相対パスで格納する
  ( cd "${root}" && zip -r "${zip_path}" "${name}/" --exclude "*.DS_Store" >/dev/null )
  success "ZIP 生成完了: ${zip_path}"
}

# ─── 1スキルを配置先へクリーンインストール（確認ゲートつき） ───────────────────
# 既存の同名ディレクトリは「削除してからコピー」する（cp -r のmerge上書きで
# 古いファイルが残るのを防ぐ）。対話時は削除前に必ず確認し、拒否ならスキップ。
# 引数: skill_dir install_dir
install_skill() {
  local skill_dir="$1" install_dir="$2"
  local name
  name="$(basename "${skill_dir}")"
  local target="${install_dir}/${name}"

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    if [[ -e "${target}" ]]; then
      dry "既存を削除して上書き: ${target}"
    else
      dry "コピー: ${skill_dir} → ${target}"
    fi
    return 0
  fi

  if [[ "${ASSUME_YES}" -ne 1 ]]; then
    if [ ! -t 0 ]; then
      warn "非対話環境のため ${name} をスキップしました。コピーするには -y を付けるか手動で配置してください:"
      warn "  rm -rf \"${target}\" && cp -r \"${skill_dir}\" \"${install_dir}/\""
      return 0
    fi
    if ! confirm "${name} を ${install_dir} へコピーしますか？"; then
      info "${name}: スキップしました"
      return 0
    fi
    if [[ -e "${target}" ]] && ! confirm "既存の ${target} を削除して上書きしますか？"; then
      info "${name}: 既存を残してスキップしました"
      return 0
    fi
  fi

  if [[ -e "${target}" || -L "${target}" ]]; then
    warn "既存の ${target} を削除します"
    rm -rf "${target}"
  fi
  mkdir -p "${install_dir}"
  cp -r "${skill_dir}" "${install_dir}/"
  # macOS の Finder が作る .DS_Store は配置先に残さない
  find "${target}" -name '.DS_Store' -type f -exec rm -f {} + 2>/dev/null || true
  success "インストール完了: ${target}"
}

# ─── 1スキルを配置先から削除（確認ゲートつき） ────────────────────────────────
# 引数: skill_dir install_dir
uninstall_skill() {
  local skill_dir="$1" install_dir="$2"
  local name
  name="$(basename "${skill_dir}")"
  local target="${install_dir}/${name}"

  if [[ ! -e "${target}" && ! -L "${target}" ]]; then
    info "${name}: ${install_dir} には入っていません（何もしません）"
    return 0
  fi
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    dry "削除: ${target}"
    return 0
  fi
  if [[ "${ASSUME_YES}" -ne 1 ]]; then
    if [ ! -t 0 ]; then
      warn "非対話環境のため ${name} の削除をスキップしました。削除するには -y を付けてください: ${target}"
      return 0
    fi
    if ! confirm "${target} を削除しますか？"; then
      info "${name}: 削除をスキップしました"
      return 0
    fi
  fi
  rm -rf "${target}"
  success "アンインストール完了: ${target}"
}

# ─── --target の値を検証し、空白区切りの一覧に展開する ────────────────────────
# 引数: カンマ区切りの target 指定   出力: 空白区切り（重複なし）
expand_targets() {
  local spec="$1" t out="" item
  local old_ifs="${IFS}"
  IFS=','
  # shellcheck disable=SC2086
  set -- ${spec}
  IFS="${old_ifs}"
  for item in "$@"; do
    # 前後の空白を除去
    item="$(printf '%s' "${item}" | tr -d '[:space:]')"
    [[ -z "${item}" ]] && continue
    case "${item}" in
      all) for t in ${ALL_TARGETS}; do
             case " ${out} " in *" ${t} "*) ;; *) out="${out} ${t}" ;; esac
           done ;;
      claude|copilot|agents|antigravity)
           case " ${out} " in *" ${item} "*) ;; *) out="${out} ${item}" ;; esac ;;
      claude.ai|claudeai|claude-ai)
           error "Claude.ai（Web）はファイルを置く方式ではありません。--zip で ZIP を作り、「カスタマイズ > スキル」に登録してください。"
           return 1 ;;
      *)   error "不明な --target: ${item}（claude | copilot | agents | antigravity | all）"
           return 1 ;;
    esac
  done
  if [[ -z "${out}" ]]; then
    error "--target が空です"
    return 1
  fi
  printf '%s\n' "${out# }"
}

# ─── エントリポイント ─────────────────────────────────────────────────────────
main() {
  local mode="install"
  local target_spec="claude"
  local scope="user"
  local project_dir=""
  local custom_dir=""

  # 引数解析
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --zip)           mode="zip" ;;
      --uninstall)     mode="uninstall" ;;
      --list-targets)  mode="list-targets" ;;
      --dry-run)       DRY_RUN=1 ;;
      -y|--yes)        ASSUME_YES=1 ;;
      -h|--help)       usage; exit 0 ;;
      -t|--target|--scope|--project-dir|--dir)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          error "$1 には値が必要です"; exit 1
        fi
        case "$1" in
          -t|--target)   target_spec="$2" ;;
          --scope)       scope="$2" ;;
          --project-dir) project_dir="$2" ;;
          --dir)         custom_dir="$2" ;;
        esac
        shift ;;
      --target=*)      target_spec="${1#*=}" ;;
      --scope=*)       scope="${1#*=}" ;;
      --project-dir=*) project_dir="${1#*=}" ;;
      --dir=*)         custom_dir="${1#*=}" ;;
      *)               error "不明な引数: $1"; usage; exit 1 ;;
    esac
    shift
  done

  case "${scope}" in
    user|project) ;;
    *) error "不明な --scope: ${scope}（user | project）"; exit 1 ;;
  esac

  # リポジトリルート
  local root
  root="$(resolve_root)"

  # project スコープの対象ディレクトリ（既定: 実行時のカレントディレクトリ）
  if [[ -z "${project_dir}" ]]; then
    project_dir="$(pwd)"
  fi

  if [[ "${mode}" == "list-targets" ]]; then
    list_targets "${project_dir}"
    exit 0
  fi

  # スキルディレクトリの検出（複数対応）
  local skill_dirs=() line
  # mapfile は bash 4+ 専用（macOS 標準の bash 3.2 で失敗する）ため while read で配列化する
  while IFS= read -r line; do
    [[ -n "${line}" ]] && skill_dirs+=("${line}")
  done < <(detect_skill_dirs "${root}")
  if [[ ${#skill_dirs[@]} -eq 0 ]]; then
    error "SKILL.md が見つかりません: ${root} 直下を確認してください"
    exit 1
  fi

  # 配置先の決定（install / uninstall のみ）
  local dests=() dest t targets
  if [[ "${mode}" == "install" || "${mode}" == "uninstall" ]]; then
    if [[ -n "${custom_dir}" ]]; then
      dests+=("${custom_dir%/}")
    else
      targets="$(expand_targets "${target_spec}")" || exit 1
      if [[ "${scope}" == "project" ]]; then
        if [[ ! -d "${project_dir}" ]]; then
          error "--project-dir が存在しません: ${project_dir}"
          exit 1
        fi
        project_dir="$(cd "${project_dir}" && pwd)"
        # スキルのリポジトリ自身へ入れるのは誤操作として止める
        case "${project_dir}/" in
          "${root}/"*)
            error "--scope project の対象がスキルのリポジトリ自身です: ${project_dir}"
            error "インストールしたいプロジェクトへ cd してから実行するか、--project-dir <path> を指定してください。"
            exit 1 ;;
        esac
      fi
      for t in ${targets}; do
        dest="$(dest_for "${t}" "${scope}" "${project_dir}")"
        # 同じ配置先は1回だけ（例: agents と antigravity の project）
        local seen=0 e
        for e in ${dests[@]+"${dests[@]}"}; do
          [[ "${e}" == "${dest}" ]] && seen=1
        done
        [[ "${seen}" -eq 0 ]] && dests+=("${dest}")
      done
    fi
  fi

  local d
  info "スキル検出: ${#skill_dirs[@]} 件"
  for d in "${skill_dirs[@]}"; do
    info "  - $(basename "${d}")"
  done

  # ファイル一覧（インストール・ZIP のときのみ）
  if [[ "${mode}" == "install" || "${mode}" == "zip" ]]; then
    echo ""
    info "ファイル一覧:"
    for d in "${skill_dirs[@]}"; do
      list_files "${d}" "${root}"
    done
  fi
  echo ""

  # モード分岐（スキルごとに処理）
  case "${mode}" in
    zip)
      if ! command -v zip &>/dev/null; then
        error "zip コマンドが見つかりません。インストールして再実行してください。"
        exit 1
      fi
      local timestamp
      timestamp="$(date '+%y%m%d-%H%M')"
      for d in "${skill_dirs[@]}"; do
        build_zip "${root}" "$(basename "${d}")" "${timestamp}"
      done
      ;;
    install)
      for dest in "${dests[@]}"; do
        info "配置先: ${dest}"
        for d in "${skill_dirs[@]}"; do
          install_skill "${d}" "${dest}"
        done
      done
      ;;
    uninstall)
      for dest in "${dests[@]}"; do
        info "配置先: ${dest}"
        for d in "${skill_dirs[@]}"; do
          uninstall_skill "${d}" "${dest}"
        done
      done
      ;;
  esac
}

main "$@"
