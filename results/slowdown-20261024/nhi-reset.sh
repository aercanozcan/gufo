#!/usr/bin/env bash
# One-host USB4 NHI ACPI reset. Root-only. LOCAL actions only (no network).
#
# Phases:
#   unload  stop service, unload thunderbolt_ibverbs (sanctioned helper), unload thunderbolt
#   reset   ACPI-reset the two USB4 NHI PCI functions (requires thunderbolt unloaded)
#   load    modprobe thunderbolt, require both NHIs to bind, report links
#   status  print current state only
#
# Usage: sudo bash nhi-reset.sh <phase> <rank 0|1> <receipts-dir>
#
# This is a NEW, separate operation. It does not modify the frozen recovery
# bundle, the Donnerkeule module, the kernel, module parameters, or transport
# mode. It never touches the PCIe USB4 bridges (00:01.1/.2, which advertise
# only "pm") nor any other PCI function.
set -euo pipefail
umask 022

PHASE=${1:?usage: nhi-reset.sh unload|reset|load|status <rank> <receipts-dir>}
RANK=${2:?rank 0 or 1}
RECV=${3:?receipts directory}
BDFS=(0000:c7:00.5 0000:c7:00.6)
# AMD Strix Halo USB4 Host Router: vendor 0x1022, USB controller class 0x0c0340.
EXPECT_VENDOR=0x1022
EXPECT_CLASS=0x0c0340

[[ $EUID -eq 0 ]] || { echo 'FATAL: run with sudo' >&2; exit 1; }
[[ $RANK == 0 || $RANK == 1 ]] || { echo 'FATAL: rank must be 0 or 1' >&2; exit 1; }
[[ $PHASE == unload || $PHASE == reset || $PHASE == load || $PHASE == status ]] || {
  echo 'FATAL: unknown phase' >&2; exit 1; }

TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$RECV"
LOG="$RECV/rank${RANK}-${PHASE}-${TS}.log"
exec > >(tee -a "$LOG") 2>&1

say() { echo "[nhi-reset rank$RANK $PHASE] $*"; }
fatal() { say "FATAL: $*"; exit 1; }

# Authoritative module-state checks (no pipeline; also handles a builtin module).
have_mod() { [[ -d /sys/module/$1 ]]; }
tb_loaded() { [[ -d /sys/module/thunderbolt ]]; }

snapshot() {
  say "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  say "uname=$(uname -r)"
  local mod
  mod=$(modinfo -n thunderbolt 2>/dev/null || echo none)
  say "thunderbolt_module=$mod"
  if [[ -f $mod ]]; then say "thunderbolt_sha256=$(sha256sum "$mod" | awk '{print $1}')"; fi
  if [[ -d /sys/module/thunderbolt/parameters ]]; then
    local f
    for f in /sys/module/thunderbolt/parameters/*; do
      if [[ -e $f ]]; then say "tbparam_${f##*/}=$(cat "$f")"; fi
    done
  fi
  local b p
  for b in "${BDFS[@]}"; do
    p=/sys/bus/pci/devices/$b
    if [[ -d $p ]]; then
      say "dev[$b] present vendor=$(cat "$p/vendor") device=$(cat "$p/device") class=$(cat "$p/class")"
      say "reset_method[$b]=$(cat "$p/reset_method" 2>/dev/null || echo '?')"
      say "driver[$b]=$( [[ -e $p/driver ]] && basename "$(readlink -f "$p/driver")" || echo none)"
      say "power[$b]=$(cat "$p/power/runtime_status" 2>/dev/null || echo '?')"
    else
      say "dev[$b] ABSENT"
    fi
  done
  say "lsmod_thunderbolt:"
  lsmod | grep -E '^thunderbolt' || say '  (none)'
  local d n
  say 'links:'
  for d in /sys/bus/thunderbolt/devices/*/; do
    n=$(basename "$d")
    if [[ -f $d/rx_speed ]]; then
      say "  $n rx=$(cat "$d/rx_speed") tx=$(cat "$d/tx_speed") rx_lanes=$(cat "$d/rx_lanes") tx_lanes=$(cat "$d/tx_lanes")"
    fi
  done
}

phase_unload() {
  say 'stopping service'
  systemctl stop "evox2-gufo@${RANK}.service" 2>/dev/null || true

  local helper=/usr/local/lib/evox2-recovery-rank${RANK}/scripts/recovery-helper.py
  [[ -f $helper ]] || fatal "installed recovery helper not found: $helper"
  say "sanctioned down via $helper"
  /usr/bin/python3 -I "$helper" down || fatal 'recovery-helper down failed'

  if have_mod thunderbolt_net; then
    say 'rmmod thunderbolt_net'
    rmmod thunderbolt_net || say 'WARN: rmmod thunderbolt_net failed'
  fi
  if tb_loaded; then
    say 'rmmod thunderbolt'
    rmmod thunderbolt || {
      say 'WARN: rmmod thunderbolt failed; likely holder is thunderbolt_net / NetworkManager on thunderbolt0'
      lsmod | grep -E '^thunderbolt' || true
      fatal 'thunderbolt still in use'
    }
  fi
  sleep 1
  tb_loaded && fatal 'thunderbolt still loaded after unload'
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs still loaded after unload'
  say 'unloaded: thunderbolt_ibverbs absent, thunderbolt absent'
}

phase_reset() {
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs is loaded; run unload first'
  tb_loaded && fatal 'thunderbolt is loaded; run unload first'
  [[ -f $(modinfo -n thunderbolt 2>/dev/null) ]] || fatal 'thunderbolt is builtin/unknown; refusing'

  local b p orig rc v
  for b in "${BDFS[@]}"; do
    tb_loaded && fatal 'thunderbolt reloaded during reset; aborting'
    p=/sys/bus/pci/devices/$b
    [[ -d $p ]] || fatal "$b absent"
    [[ "$(cat "$p/class")" == "$EXPECT_CLASS" ]] || fatal "$b is not a USB4 NHI (class $(cat "$p/class"))"
    [[ "$(cat "$p/vendor")" == "$EXPECT_VENDOR" ]] || fatal "$b unexpected vendor $(cat "$p/vendor")"
    [[ -e $p/driver ]] && fatal "$b still bound to a driver"
    [[ -w $p/reset ]] || fatal "$b has no writable reset"
    orig=$(cat "$p/reset_method" 2>/dev/null) || fatal "$b reset_method unreadable"
    case " $orig " in
      *" acpi "*) : ;;
      *) fatal "$b reset_method lacks acpi: '$orig'" ;;
    esac

    say "ACPI reset $b (advertised: $orig)"
    printf 'acpi' > "$p/reset_method" || fatal "$b reset_method write failed"
    set +e
    printf '1' > "$p/reset"
    rc=$?
    set -e
    if [[ -e $p/reset_method ]]; then
      printf '%s' "$orig" > "$p/reset_method" || say "WARN: could not restore reset_method on $b"
    else
      say "WARN: $b reset_method vanished during reset"
    fi
    say "reset[$b] rc=$rc"
    [[ $rc -eq 0 ]] || fatal "reset of $b failed rc=$rc; do not run load; this host likely needs a reboot"
    [[ -d $p ]] || fatal "$b vanished after reset; reboot needed"
    v=$(cat "$p/vendor" 2>/dev/null || echo 0xffff)
    [[ -n $v && $v != 0xffff ]] || fatal "$b unresponsive after reset (vendor=$v); reboot needed"
    say "post[$b] vendor=$v reset_method=$(cat "$p/reset_method" 2>/dev/null || echo '?')"
  done
  say 'both NHI functions ACPI-reset'
}

phase_load() {
  say 'modprobe thunderbolt'
  modprobe thunderbolt || fatal 'modprobe thunderbolt failed'

  local i b p bound=0 links=0 d
  for i in $(seq 1 30); do
    bound=0
    for b in "${BDFS[@]}"; do
      if [[ -e /sys/bus/pci/devices/$b/driver ]]; then bound=$((bound+1)); fi
    done
    [[ $bound -eq 2 ]] && break
    sleep 1
  done
  say "NHI bound to a driver: $bound/2"
  [[ $bound -eq 2 ]] || fatal "only $bound/2 NHI functions bound after load; reboot likely needed"

  for i in $(seq 1 60); do
    links=0
    for d in /sys/bus/thunderbolt/devices/*/; do
      if [[ -f $d/rx_speed ]]; then links=$((links+1)); fi
    done
    [[ $links -ge ${#BDFS[@]} ]] && break
    sleep 1
  done
  say "links enumerated: $links/${#BDFS[@]}"
  snapshot
  if [[ $links -lt ${#BDFS[@]} ]]; then
    say 'WARN: not all links enumerated yet; they train once the peer is loaded too'
  fi
  say 'load done'
}

case "$PHASE" in
  status) snapshot ;;
  unload) snapshot; phase_unload ;;
  reset)  phase_reset ;;
  load)   phase_load ;;
esac
say "OK phase=$PHASE rank=$RANK log=$LOG"
