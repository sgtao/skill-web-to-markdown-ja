#!/usr/bin/env bash
# =============================================================================
# install.sh — スキルのインストール支援スクリプト
#
# Usage:
#   bash scripts/install.sh              # ファイル一覧表示 → ~/.claude/skills へコピー（確認あり）
#   bash scripts/install.sh -y           # 確認なしでコピー（CI / 自動化向け）
#   bash scripts/install.sh --zip        # dist/ 配下に配布用 ZIP を生成
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

# ─── ヘルプ ───────────────────────────────────────────────────────────────────
usage() {
  cat <<'EOF'
install.sh — websearch-slide-ja スキルのインストール支援

Usage:
  bash scripts/install.sh           ~/.claude/skills へコピー（確認あり）
  bash scripts/install.sh -y        確認なしでコピー
  bash scripts/install.sh --zip     dist/ 配下に配布用 ZIP を生成
  bash scripts/install.sh --help    このヘルプを表示

Environment:
  CLAUDE_SKILLS_DIR   インストール先を上書き（デフォルト: ~/.claude/skills）
EOF
}

# ─── リポジトリルート（scripts/ の1つ上）を解決 ───────────────────────────────
resolve_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && cd .. && pwd
}

# ─── SKILL.md を持つディレクトリを直下1階層から検出（標準出力に返す） ──────────
detect_skill_dir() {
  local root="$1" d
  for d in "${root}"/*/; do
    if [[ -f "${d}SKILL.md" ]]; then
      printf '%s\n' "${d%/}"
      return 0
    fi
  done
  return 1
}

# ─── スキルディレクトリのファイル一覧を表示（ルート相対） ──────────────────────
list_files() {
  local skill_dir="$1" root="$2"
  find "${skill_dir}" -type f | sort | sed "s|${root}/||"
}

# ─── 配布用 ZIP を dist/<name>-YYMMDD-HHMM.zip に生成 ─────────────────────────
build_zip() {
  local root="$1" name="$2"
  if ! command -v zip &>/dev/null; then
    error "zip コマンドが見つかりません。インストールして再実行してください。"
    return 1
  fi
  local timestamp dist_dir zip_path
  timestamp="$(date '+%y%m%d-%H%M')"
  dist_dir="${root}/dist"
  zip_path="${dist_dir}/${name}-${timestamp}.zip"
  mkdir -p "${dist_dir}"
  # ZIP はリポジトリルートから相対パスで格納する
  ( cd "${root}" && zip -r "${zip_path}" "${name}/" --exclude "*.DS_Store" >/dev/null )
  success "ZIP 生成完了: ${zip_path}"
}

# ─── ~/.claude/skills へコピー（確認ゲートつき） ──────────────────────────────
install_skill() {
  local skill_dir="$1" name="$2"
  local install_dir="${CLAUDE_SKILLS_DIR:-$INSTALL_DIR_DEFAULT}"

  if [[ "${ASSUME_YES}" -ne 1 ]]; then
    if [ ! -t 0 ]; then
      warn "非対話環境のため確認をスキップしました。コピーするには -y を付けるか手動で配置してください:"
      warn "  cp -r \"${skill_dir}\" \"${install_dir}/\""
      return 0
    fi
    local answer
    printf "%b[CONFIRM]%b %s を %s へコピーしますか？ [y/N] " \
          "${C_BOLD}" "${C_RESET}" "${name}" "${install_dir}"
    read -r answer
    if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
      info "コピーをスキップしました"
      return 0
    fi
  fi

  mkdir -p "${install_dir}"
  [[ -d "${install_dir}/${name}" ]] && warn "既存の ${name} を上書きします"
  cp -r "${skill_dir}" "${install_dir}/"
  success "インストール完了: ${install_dir}/${name}"
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

  # リポジトリルートとスキルディレクトリの検出
  local root skill_dir name
  root="$(resolve_root)"
  if ! skill_dir="$(detect_skill_dir "${root}")"; then
    error "SKILL.md が見つかりません: ${root} 直下を確認してください"
    exit 1
  fi
  name="$(basename "${skill_dir}")"
  info "スキル検出: ${name} (${skill_dir})"

  # ファイル一覧（共通）
  echo ""
  info "ファイル一覧:"
  list_files "${skill_dir}" "${root}"
  echo ""

  # モード分岐
  case "${mode}" in
    zip)     build_zip "${root}" "${name}" ;;
    install) install_skill "${skill_dir}" "${name}" ;;
  esac
}

main "$@"
