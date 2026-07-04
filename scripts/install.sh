#!/usr/bin/env bash
# =============================================================================
# install.sh — Claude Code スキルのインストール支援スクリプト（汎用）
#
# リポジトリ直下1階層から SKILL.md を持つディレクトリをすべて検出し、
# スキルごとに確認しながら ~/.claude/skills へクリーンインストールする。
#
# Usage:
#   bash scripts/install.sh              # スキルごとに確認してコピー
#   bash scripts/install.sh -y           # 確認なしでコピー（既存は削除して上書き）
#   bash scripts/install.sh --zip        # dist/ 配下にスキルごとの配布用 ZIP を生成
#   bash scripts/install.sh --help       # ヘルプ表示
#
# Environment variables:
#   CLAUDE_SKILLS_DIR  - インストール先を上書き（デフォルト: ~/.claude/skills）
# =============================================================================
set -euo pipefail

# ─── 設定 ─────────────────────────────────────────────────────────────────────
INSTALL_DIR_DEFAULT="${HOME}/.claude/skills"
ASSUME_YES=0   # -y / --yes で 1 になる

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
install.sh — Claude Code スキルのインストール支援

リポジトリ直下から SKILL.md を持つディレクトリをすべて検出し、
スキルごとに確認しながらインストール / ZIP 生成する。

Usage:
  bash scripts/install.sh           ~/.claude/skills へコピー（スキルごとに確認）
  bash scripts/install.sh -y        確認なしでコピー（既存は削除して上書き）
  bash scripts/install.sh --zip     dist/ 配下にスキルごとの配布用 ZIP を生成
  bash scripts/install.sh --help    このヘルプを表示

Environment:
  CLAUDE_SKILLS_DIR   インストール先を上書き（デフォルト: ~/.claude/skills）
EOF
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
  mkdir -p "${dist_dir}"
  # ZIP はリポジトリルートから相対パスで格納する
  ( cd "${root}" && zip -r "${zip_path}" "${name}/" --exclude "*.DS_Store" >/dev/null )
  success "ZIP 生成完了: ${zip_path}"
}

# ─── 1スキルを ~/.claude/skills へクリーンインストール（確認ゲートつき） ───────
# 既存の同名ディレクトリは「削除してからコピー」する（cp -r のmerge上書きで
# 古いファイルが残るのを防ぐ）。対話時は削除前に必ず確認し、拒否ならスキップ。
install_skill() {
  local skill_dir="$1"
  local name
  name="$(basename "${skill_dir}")"
  local install_dir="${CLAUDE_SKILLS_DIR:-$INSTALL_DIR_DEFAULT}"
  local target="${install_dir}/${name}"

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

  if [[ -e "${target}" ]]; then
    warn "既存の ${target} を削除します"
    rm -rf "${target}"
  fi
  mkdir -p "${install_dir}"
  cp -r "${skill_dir}" "${install_dir}/"
  success "インストール完了: ${target}"
}

# ─── エントリポイント ─────────────────────────────────────────────────────────
main() {
  local mode="install"

  # 引数解析
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --zip)      mode="zip" ;;
      -y|--yes)   ASSUME_YES=1 ;;
      -h|--help)  usage; exit 0 ;;
      *)          error "不明な引数: $1"; usage; exit 1 ;;
    esac
    shift
  done

  # リポジトリルートとスキルディレクトリの検出（複数対応）
  local root
  root="$(resolve_root)"
  local skill_dirs=()
  mapfile -t skill_dirs < <(detect_skill_dirs "${root}")
  if [[ ${#skill_dirs[@]} -eq 0 ]]; then
    error "SKILL.md が見つかりません: ${root} 直下を確認してください"
    exit 1
  fi

  local d
  info "スキル検出: ${#skill_dirs[@]} 件"
  for d in "${skill_dirs[@]}"; do
    info "  - $(basename "${d}")"
  done

  # ファイル一覧（共通）
  echo ""
  info "ファイル一覧:"
  for d in "${skill_dirs[@]}"; do
    list_files "${d}" "${root}"
  done
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
      for d in "${skill_dirs[@]}"; do
        install_skill "${d}"
      done
      ;;
  esac
}

main "$@"
