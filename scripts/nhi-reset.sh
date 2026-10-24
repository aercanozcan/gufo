#!/usr/bin/env bash
# One-host USB4 NHI ACPI reset. Root-only, local actions only (no network).
#
# This file is installed into the frozen recovery bundle and hash-pinned by
# manifest.json. It is invoked only through the exact recovery-helper grants
# `nhi-unload`, `nhi-reset` and `nhi-load`; the helper stops the service,
# enforces the ownership/kernel grants and removes the pinned RDMA driver
# before the `unload` phase runs.
#
# Phases:
#   unload  unload thunderbolt (the RDMA driver is already gone) and prove absent
#   reset   ACPI-reset the two USB4 NHI PCI functions (requires thunderbolt off)
#   load    modprobe thunderbolt, require both NHIs to bind, report links
#
# Usage: nhi-reset.sh unload|reset|load <rank 0|1> <receipts-dir>
#
# It never touches the PCIe USB4 bridges (00:01.1/.2, which advertise only
# "pm") nor any other PCI function, module parameter, kernel or config file.
set -euo pipefail
umask 022

PHASE=${1:?usage: nhi-reset.sh unload|reset|load <rank> <receipts-dir>}
RANK=${2:?rank 0 or 1}
RECV=${3:?receipts directory}
BDFS=(0000:c7:00.5 0000:c7:00.6)
# AMD Strix Halo USB4 Host Router: vendor 0x1022, USB controller class 0x0c0340.
EXPECT_VENDOR=0x1022
EXPECT_CLASS=0x0c0340

[[ $EUID -eq 0 ]] || { echo 'FATAL: run as root through the exact recovery grant' >&2; exit 1; }
case $RANK in 0|1) ;; *) echo 'FATAL: rank must be 0 or 1' >&2; exit 1 ;; esac
case $PHASE in unload|reset|load) ;; *) echo 'FATAL: unknown phase' >&2; exit 1 ;; esac

TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p -m 0755 "$RECV"
LOG="$RECV/rank${RANK}-${PHASE}-${TS}.log"
exec > >(tee -a "$LOG") 2>&1

say() { echo "[nhi-reset rank$RANK $PHASE] $*"; }
fatal() { say "FATAL: $*"; exit 1; }

have_mod() { [[ -d /sys/module/$1 ]]; }
tb_loaded() { have_mod thunderbolt; }

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
      say "dev[$b] vendor=$(cat "$p/vendor") device=$(cat "$p/device") class=$(cat "$p/class")"
      say "reset_method[$b]=$(cat "$p/reset_method" 2>/dev/null || echo '?')"
      say "driver[$b]=$( [[ -e $p/driver ]] && basename "$(readlink -f "$p/driver")" || echo none)"
      say "power[$b]=$(cat "$p/power/runtime_status" 2>/dev/null || echo '?')"
    else
      say "dev[$b] ABSENT"
    fi
  done
  say 'links:'
  local d n
  for d in /sys/bus/thunderbolt/devices/*/; do
    n=$(basename "$d")
    if [[ -f $d/rx_speed ]]; then
      say "  $n rx=$(cat "$d/rx_speed") tx=$(cat "$d/tx_speed") rx_lanes=$(cat "$d/rx_lanes") tx_lanes=$(cat "$d/tx_lanes")"
    fi
  done
}

phase_unload() {
  say 'the exact recovery helper already ran the pinned driver down step'
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs is still loaded'
  if have_mod thunderbolt_net; then
    say 'rmmod thunderbolt_net'
    rmmod thunderbolt_net || say 'WARN: rmmod thunderbolt_net failed'
  fi
  if tb_loaded; then
    say 'rmmod thunderbolt'
    rmmod thunderbolt || {
      lsmod | grep -E '^thunderbolt' || true
      fatal 'thunderbolt is still in use; close Thunderbolt networking and retry'
    }
  fi
  sleep 1
  tb_loaded && fatal 'thunderbolt still loaded after unload'
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs still loaded after unload'
  say 'unloaded: thunderbolt_ibverbs absent, thunderbolt absent'
}

phase_reset() {
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs is loaded; run nhi-unload first'
  tb_loaded && fatal 'thunderbolt is loaded; run nhi-unload first'
  local module
  module=$(modinfo -n thunderbolt 2>/dev/null || true)
  [[ -n $module && -f $module ]] || fatal 'thunderbolt is builtin or missing; refusing'
  say "module=$module"
  say "boot_id=$(cat /proc/sys/kernel/random/boot_id)"

  local b p orig rc v
  for b in "${BDFS[@]}"; do
    tb_loaded && fatal 'thunderbolt reloaded during the reset; aborting'
    p=/sys/bus/pci/devices/$b
    [[ -d $p ]] || fatal "$b absent"
    [[ "$(cat "$p/class" 2>/dev/null)" == "$EXPECT_CLASS" ]] || fatal "$b is not a USB4 NHI"
    [[ "$(cat "$p/vendor" 2>/dev/null)" == "$EXPECT_VENDOR" ]] || fatal "$b has an unexpected vendor"
    [[ -e $p/driver ]] && fatal "$b is still bound to a driver"
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
      say "WARN: $b reset_method vanished during the reset"
    fi
    say "reset[$b] rc=$rc"
    [[ $rc -eq 0 ]] || fatal "reset of $b failed rc=$rc; this host needs a reboot"
    [[ -d $p ]] || fatal "$b vanished after the reset; this host needs a reboot"
    v=$(cat "$p/vendor" 2>/dev/null || echo 0xffff)
    [[ -n $v && $v != 0xffff ]] || fatal "$b unresponsive after the reset; this host needs a reboot"
    say "post[$b] vendor=$v reset_method=$(cat "$p/reset_method" 2>/dev/null || echo '?')"
  done
  say 'both NHI functions ACPI-reset'
}

phase_load() {
  have_mod thunderbolt_ibverbs && fatal 'thunderbolt_ibverbs is loaded; run nhi-unload first'
  tb_loaded && fatal 'thunderbolt is already loaded; run nhi-unload first'
  say 'modprobe thunderbolt'
  modprobe thunderbolt || fatal 'modprobe thunderbolt failed'

  local i b bound=0 links=0 d
  for i in $(seq 1 30); do
    bound=0
    for b in "${BDFS[@]}"; do
      if [[ -e /sys/bus/pci/devices/$b/driver ]]; then bound=$((bound + 1)); fi
    done
    [[ $bound -eq 2 ]] && break
    sleep 1
  done
  say "NHI bound to a driver: $bound/2"
  [[ $bound -eq 2 ]] || fatal "only $bound/2 NHI functions bound after load; this host needs a reboot"

  for i in $(seq 1 30); do
    links=0
    for d in /sys/bus/thunderbolt/devices/*/; do
      if [[ -f $d/rx_speed ]]; then links=$((links + 1)); fi
    done
    [[ $links -ge 2 ]] && break
    sleep 1
  done
  say "links enumerated: $links/2"
  snapshot
  if [[ $links -lt 2 ]]; then
    say 'WARN: links not enumerated yet; they train once the peer host is loaded too'
  fi
  say 'load done'
}

say "start rank=$RANK phase=$PHASE"
snapshot
case "$PHASE" in
  unload) phase_unload ;;
  reset)  phase_reset ;;
  load)   phase_load ;;
esac
say "OK phase=$PHASE rank=$RANK log=$LOG"
