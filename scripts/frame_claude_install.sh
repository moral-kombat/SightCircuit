#!/usr/bin/env bash
# Phase 0 of docs/agent-on-frame.md: install and verify Claude Code on Steam Frame,
# with a status check after every step and a breakpoint before every stage.
#
#   bash frame_claude_install.sh [stage ...]
#
# Stages (default: all, in this order):
#   precheck   architecture, OS, glibc, free space, curl, user, SSH session
#   install    official native installer, stable channel, into ~/.local (no sudo)
#   path       make sure ~/.local/bin is on PATH (asks before editing ~/.bashrc)
#   verify     claude --version and claude doctor; detects the known aarch64 abort
#   signature  optional: check the installed binary against Anthropic's signed manifest
#   login      interactive sign-in (prints a URL to open on your PC)
#   smoke      one bounded headless prompt in a scratch folder: must report aarch64
#   record     write the evidence summary
#
# Breakpoints: before each stage press Enter to run it, s to skip it, q to stop.
# Environment: YES=1 runs without breakpoints (login is then skipped),
#   CHANNEL=stable|latest|<version> (default stable), OUT=<dir> for the reports
#   (default ~/claude-phase0/<UTC time>), STEP_TIMEOUT=<seconds> (default 300).
#
# Never uses sudo, never touches the system partition, SteamVR, cameras or the
# framejacker checkout. Results are a user-run device report; see the summary file.
set -uo pipefail

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${OUT:-$HOME/claude-phase0/$stamp}"
channel="${CHANNEL:-stable}"
limit="${STEP_TIMEOUT:-300}"
# Anthropic's Claude Code release signing key (primary key fingerprint, as published
# in the Claude Code setup documentation). The signature must come from this key.
release_key=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
mkdir -p "$out"
summary="$out/summary.md"
log="$out/commands.log"
failures=0
warnings=0

if [ -t 1 ]; then green=$'\e[32m'; yellow=$'\e[33m'; red=$'\e[31m'; bold=$'\e[1m'; plain=$'\e[0m'
else green=""; yellow=""; red=""; bold=""; plain=""; fi

note() { printf '%s\n' "$*" >> "$summary"; }
pass() { printf '%s[PASS]%s %s\n' "$green" "$plain" "$*"; note "- PASS: $*"; }
warn() { printf '%s[WARN]%s %s\n' "$yellow" "$plain" "$*"; note "- WARN: $*"; warnings=$((warnings + 1)); }
fail() { printf '%s[FAIL]%s %s\n' "$red" "$plain" "$*"; note "- FAIL: $*"; failures=$((failures + 1)); }
info() { printf '       %s\n' "$*"; }

capture() { # capture <label> <command...>: run with a timeout, log output, set $captured and $code
  local label="$1"; shift
  printf '\n$ %s\n' "$*" >> "$log"
  captured="$(timeout -k 10 "$limit" "$@" 2>&1)"; code=$?
  printf '%s\n[exit %s]\n' "$captured" "$code" >> "$log"
  [ "$code" = 124 ] && warn "$label timed out after ${limit}s"
  return "$code"
}

breakpoint() { # breakpoint <stage> <description>; returns 1 to skip
  printf '\n%s== %s%s: %s\n' "$bold" "$1" "$plain" "$2"
  [ -n "${YES:-}" ] && return 0
  local reply
  read -r -p "   Enter = run, s = skip, q = stop here: " reply || reply=q
  case "$reply" in
    s|S) note "- SKIPPED: $1 (by you)"; return 1 ;;
    q|Q) note "- STOPPED before $1 (by you)"; finish ;;
  esac
  return 0
}

stop_now() { # a failure that makes later stages meaningless
  fail "$1"
  info "Stopping. Send $summary and $log to the development session."
  finish
}

finish() {
  note ""
  note "Outcome: $failures failure(s), $warnings warning(s). Reports: $out"
  printf '\n%sSummary%s (%s)\n' "$bold" "$plain" "$summary"
  cat "$summary"
  [ "$failures" = 0 ] && exit 0 || exit 1
}

find_claude() {
  if command -v claude >/dev/null 2>&1; then command -v claude
  elif [ -x "$HOME/.local/bin/claude" ]; then echo "$HOME/.local/bin/claude"
  else return 1; fi
}

stage_precheck() {
  breakpoint precheck "read-only checks of this machine" || return 0
  local machine; machine="$(uname -m)"
  if [ "$machine" = aarch64 ]; then pass "architecture aarch64"
  else warn "architecture is $machine, not aarch64: is this really the Frame?"; fi
  local host; host="$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null)"
  info "hostname: $host"; note "  - hostname: $host"
  if [ -r /etc/os-release ]; then
    local os; os="$(grep -E '^(PRETTY_NAME|VERSION_ID|BUILD_ID)=' /etc/os-release | tr '\n' ' ')"
    info "os: $os"; note "  - os: $os"
  fi
  if [ "$(id -u)" = 0 ]; then stop_now "running as root; run as your normal user (steamos), without sudo"; fi
  pass "running as $(id -un) without root"
  if capture glibc ldd --version; then
    info "glibc: $(printf '%s\n' "$captured" | head -1)"; note "  - glibc: $(printf '%s\n' "$captured" | head -1)"
  else warn "could not read the glibc version"; fi
  local free_kb; free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
  if [ -n "$free_kb" ] && [ "$free_kb" -ge 1048576 ]; then pass "$((free_kb / 1024)) MB free in $HOME"
  else stop_now "less than 1 GB free in $HOME (${free_kb:-unknown} KB)"; fi
  if command -v curl >/dev/null 2>&1; then pass "curl available"
  else stop_now "curl not found; the official installer needs it"; fi
  if [ -n "${SSH_CONNECTION:-}" ]; then pass "running over SSH (login will print a URL to open on your PC)"
  else info "not an SSH session: if you are on the headset console, sign-in may open the headset browser"; fi
  if command -v tmux >/dev/null 2>&1; then pass "tmux available (useful for Phase 2)"
  else info "tmux not found; Phase 2 can use another way to keep a session alive"; fi
  if command -v gpg >/dev/null 2>&1; then info "gpg available: the signature stage can run"
  else info "gpg not found: the signature stage will be skipped"; fi
  if ! curl -fsS -o /dev/null --max-time 15 https://claude.ai/install.sh; then
    stop_now "cannot reach https://claude.ai from this network"
  fi
  pass "claude.ai reachable"
}

stage_install() {
  breakpoint install "official native installer, channel '$channel', into ~/.local (no sudo)" || return 0
  local existing
  if existing="$(find_claude)"; then
    capture existing-version "$existing" --version
    info "already installed at $existing: $captured"
    if [ -z "${YES:-}" ]; then
      local reply; read -r -p "   Reinstall/update anyway? [y/N] " reply || reply=n
      [ "$reply" = y ] || { note "- install: kept existing $captured"; return 0; }
    else
      note "- install: kept existing $captured"; return 0
    fi
  fi
  local script="$out/install.sh"
  if ! curl -fsSL --proto =https --proto-redir =https --max-time 60 https://claude.ai/install.sh -o "$script"; then
    stop_now "could not download the installer"
  fi
  info "installer saved to $script ($(sha256sum "$script" | cut -c1-16)...)"
  note "  - installer sha256: $(sha256sum "$script" | cut -d' ' -f1)"
  if capture installer bash "$script" "$channel"; then
    pass "installer finished"
  else
    printf '%s\n' "$captured" | tail -20
    if [ "$code" = 134 ] || grep -q Aborted <<<"$captured"; then
      stop_now "installer aborted (exit $code): this matches the reported aarch64 abort. Use option C (Claude Code on the PC) in docs/agent-on-frame.md"
    fi
    stop_now "installer failed with exit $code"
  fi
  if [ -x "$HOME/.local/bin/claude" ]; then pass "binary present at ~/.local/bin/claude"
  else stop_now "installer reported success but ~/.local/bin/claude is missing"; fi
}

stage_path() {
  breakpoint path "make ~/.local/bin available as 'claude'" || return 0
  if command -v claude >/dev/null 2>&1; then pass "claude on PATH: $(command -v claude)"; return 0; fi
  export PATH="$HOME/.local/bin:$PATH"
  if grep -qs '.local/bin' "$HOME/.bashrc"; then
    pass "~/.bashrc already mentions ~/.local/bin (open a new shell to pick it up)"; return 0
  fi
  local reply=y
  [ -n "${YES:-}" ] || { read -r -p "   Append 'export PATH=\"\$HOME/.local/bin:\$PATH\"' to ~/.bashrc? [Y/n] " reply || reply=n; }
  if [ "$reply" != n ]; then
    printf '\n# Claude Code (framejacker Phase 0)\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
    pass "added ~/.local/bin to PATH in ~/.bashrc"
  else
    warn "PATH not changed; use ~/.local/bin/claude or add it yourself"
  fi
}

stage_verify() {
  breakpoint verify "claude --version and claude doctor" || return 0
  local claude_bin; claude_bin="$(find_claude)" || stop_now "claude not found; run the install stage"
  if capture version "$claude_bin" --version; then
    pass "claude --version: $captured"; note "  - version: $captured"
  else
    printf '%s\n' "$captured" | tail -20
    if [ "$code" = 134 ] || grep -q Aborted <<<"$captured"; then
      stop_now "claude aborted at start (exit $code): the reported aarch64 abort. Use option C in docs/agent-on-frame.md"
    fi
    stop_now "claude --version failed with exit $code"
  fi
  if capture doctor "$claude_bin" doctor; then pass "claude doctor exited 0"
  else warn "claude doctor exited $code"; fi
  printf '%s\n' "$captured" | sed 's/^/       /' | head -40
  printf '%s\n' "$captured" > "$out/doctor.txt"
  note "  - doctor output: doctor.txt"
}

stage_signature() {
  breakpoint signature "verify the installed binary against Anthropic's signed release manifest" || return 0
  command -v gpg >/dev/null 2>&1 || { warn "gpg not available; signature check skipped"; return 0; }
  command -v python3 >/dev/null 2>&1 || { warn "python3 not available; signature check skipped"; return 0; }
  local claude_bin version binary platform
  claude_bin="$(find_claude)" || stop_now "claude not found"
  if ! capture version "$claude_bin" --version; then
    warn "claude --version failed with exit $code; signature check skipped"; return 0
  fi
  version="$(printf '%s\n' "$captured" | head -1 | awk '{print $1}')"
  binary="$HOME/.local/share/claude/versions/$version"
  [ -f "$binary" ] || { warn "no installed binary at $binary; signature check skipped"; return 0; }
  # The check covers this file. The claude that runs is the one on PATH, which the native
  # installer links to this file; a launcher of your own may differ.
  note "  - checked file: $binary (claude on PATH: $claude_bin, resolving to $(readlink -f "$claude_bin"))"
  case "$(uname -m)" in aarch64) platform=linux-arm64 ;; x86_64) platform=linux-x64 ;; *) platform="" ;; esac
  local work; work="$(mktemp -d)"
  local repo=https://downloads.claude.ai/claude-code-releases
  local get=(curl -fsSL --proto =https --proto-redir =https --max-time 60)
  if ! "${get[@]}" https://downloads.claude.ai/keys/claude-code.asc -o "$work/key.asc" ||
     ! "${get[@]}" "$repo/$version/manifest.json" -o "$work/manifest.json" ||
     ! "${get[@]}" "$repo/$version/manifest.json.sig" -o "$work/manifest.json.sig"; then
    warn "could not download the key or manifest for $version"; rm -rf "$work"; return 0
  fi
  # A throwaway keyring, so your own GnuPG setup is not changed. gpg starts an agent for it;
  # the agent, its socket directory and the keyring are removed afterwards, also when the
  # stage is interrupted (Ctrl-C, a dropped SSH connection).
  export GNUPGHOME="$work/gnupg"; mkdir -m 700 "$GNUPGHOME"
  drop_keyring() {
    gpgconf --kill all >/dev/null 2>&1; gpgconf --remove-socketdir >/dev/null 2>&1
    unset GNUPGHOME; rm -rf "$work"; trap - INT TERM HUP
  }
  trap 'drop_keyring; note "- STOPPED: signature stage interrupted"; exit 130' INT
  trap 'drop_keyring; note "- STOPPED: signature stage interrupted"; exit 143' TERM
  trap 'drop_keyring; note "- STOPPED: signature stage interrupted"; exit 129' HUP
  gpg --batch --quiet --import "$work/key.asc" 2>>"$log"
  if gpg --batch --with-colons --fingerprint 2>/dev/null | grep -q "^fpr:::::::::$release_key:"; then
    pass "release key fingerprint ${release_key:0:4} ${release_key:4:4} ... ${release_key:32:4} ${release_key:36:4}"
  else
    fail "release key fingerprint does not match"; drop_keyring; return 0
  fi
  # The downloaded key file may hold other keys too. A signature counts only when gpg calls it
  # good (GOODSIG: valid, by a key neither revoked nor expired) and its VALIDSIG names the pinned
  # key as the signing key or, for a signing subkey, as its primary key. gpg exits 0 for a
  # revoked or expired key (REVKEYSIG, EXPKEYSIG), so any bad, revoked or expired signature
  # rejects the manifest.
  local status
  status="$(gpg --batch --status-fd 1 --verify "$work/manifest.json.sig" "$work/manifest.json" 2>>"$log")"
  code=$?
  printf '%s\n[exit %s]\n' "$status" "$code" >> "$log"
  if [ "$code" = 0 ] && awk -v key="$release_key" '
       $1 != "[GNUPG:]" {next}
       $2 ~ /^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG)$/ {bad=1}
       $2 == "NEWSIG" {good=0}
       $2 == "GOODSIG" {good=1}
       $2 == "VALIDSIG" && good && ($3 == key || $NF == key) {found=1}
       END {exit !(found && !bad)}' <<<"$status"; then
    pass "manifest signature good for $version, made by the pinned release key"
  else
    fail "manifest signature did not verify with the pinned release key (a revoked or expired key does not count)"
    drop_keyring; return 0
  fi
  local expected actual
  expected="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("platforms",{}).get(sys.argv[2],{}).get("checksum",""))' "$work/manifest.json" "$platform")"
  drop_keyring
  actual="$(sha256sum "$binary" | cut -d' ' -f1)"
  if [ -z "$expected" ]; then
    warn "manifest has no checksum for platform '$platform'"
  elif [ "$expected" = "$actual" ]; then
    pass "installed binary matches the signed manifest ($platform)"
  else
    fail "installed binary checksum differs from the signed manifest"
  fi
}

stage_login() {
  if [ -n "${YES:-}" ]; then note "- SKIPPED: login (needs you; YES=1 set)"; return 0; fi
  breakpoint login "interactive sign-in: open the printed URL on your PC, approve, then type /exit" || return 0
  local claude_bin; claude_bin="$(find_claude)" || stop_now "claude not found"
  local scratch="$HOME/claude-scratch"; mkdir -p "$scratch"
  ( cd "$scratch" && "$claude_bin" )
  local reply; read -r -p "   Did sign-in complete? [y/N] " reply || reply=n
  if [ "$reply" = y ]; then pass "sign-in completed (user-reported)"; else warn "sign-in not confirmed"; fi
}

stage_smoke() {
  breakpoint smoke "one headless prompt in ~/claude-scratch; allowed to run only uname and hostname" || return 0
  local claude_bin; claude_bin="$(find_claude)" || stop_now "claude not found"
  local scratch="$HOME/claude-scratch"; mkdir -p "$scratch"
  if capture smoke bash -c 'cd "$1" && "$2" -p "Run uname -m and hostname, then reply with exactly two lines: the uname output and the hostname output." --allowedTools "Bash(uname:*)" "Bash(hostname:*)"' _ "$scratch" "$claude_bin"; then
    printf '%s\n' "$captured" | sed 's/^/       /'
    printf '%s\n' "$captured" > "$out/smoke.txt"
    if grep -q aarch64 <<<"$captured"; then pass "the agent ran a command on this machine and reported aarch64"
    else warn "agent replied, but aarch64 was not in its answer (see smoke.txt)"; fi
  else
    printf '%s\n' "$captured" | tail -20 | sed 's/^/       /'
    if grep -qiE 'log ?in|auth|credential|api key' <<<"$captured"; then
      fail "headless prompt failed: not signed in? Run the login stage"
    else
      fail "headless prompt failed with exit $code (see commands.log)"
    fi
  fi
}

stage_record() {
  breakpoint record "write the evidence summary" || return 0
  {
    echo "  - recorded: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "  - kernel: $(uname -srm)"
  } >> "$summary"
  local claude_bin
  if claude_bin="$(find_claude)"; then
    if capture version "$claude_bin" --version; then note "  - claude: $(printf '%s\n' "$captured" | head -1)"
    else note "  - claude: --version failed with exit $code"; fi
  fi
  pass "summary written"
}

{
  echo "# Claude Code Phase 0 on Frame, $stamp"
  echo
  echo "User-run device report from scripts/frame_claude_install.sh (channel: $channel)."
  echo "Command output: commands.log. Not a cloud check; sign-in is user-reported."
  echo
} > "$summary"

stages=("$@")
[ ${#stages[@]} -eq 0 ] && stages=(precheck install path verify signature login smoke record)
for stage in "${stages[@]}"; do
  case "$stage" in
    precheck|install|path|verify|signature|login|smoke|record) "stage_$stage" ;;
    *) echo "Unknown stage: $stage (see the top of this script)" >&2; exit 2 ;;
  esac
done
finish
