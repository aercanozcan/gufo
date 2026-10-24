#!/usr/bin/env bash
# Orchestrate the USB4 NHI ACPI reset across both hosts from rank 0.
#
# Run as the NORMAL user on rank 0 in a visible terminal:
#     bash results/slowdown-20261024/nhi-reset-orchestrate.sh
#
# - Read-only preflight on both hosts BEFORE any mutation.
# - rank 0 phases run via `sudo` (prompts in this terminal).
# - rank 1 phases run over the pinned SSH config via `ssh -t`, so sudo can
#   prompt there; type the rank-1 password when asked. Nothing is stored.
# - The rank-1 runner lives in a private 0700 mktemp dir and its sha256 is
#   re-verified before every phase.
# - Order per phase across hosts => both UNLOADED before either RESET,
#   both RESET before either LOADED.
# - Does not start services. Use the normal sanctioned startup afterwards.
set -euo pipefail

SSHCFG=${SSHCFG:-/home/ercanozcan/Desktop/llm/evox2-cluster-rerun-20261024/.private/ssh_config}
R1=${R1:-ercan@192.168.0.217}
ROOT=/home/ercanozcan/Desktop/llm/evox2-cluster
SCRIPT=$ROOT/results/slowdown-20261024/nhi-reset.sh
RECV=$ROOT/results/slowdown-20261024/nhi-reset-receipts
BDFS="0000:c7:00.5 0000:c7:00.6"

[[ -f $SCRIPT ]] || { echo "FATAL: missing $SCRIPT" >&2; exit 1; }
[[ -f $SSHCFG ]] || { echo "FATAL: missing pinned ssh config $SSHCFG" >&2; exit 1; }
mkdir -p "$RECV"
R1DIR=$(ssh -F "$SSHCFG" -T -o BatchMode=yes "$R1" 'mktemp -d /tmp/nhi-reset.XXXXXX')
R1SCRIPT=$R1DIR/nhi-reset.sh
R1RECV=$R1DIR/receipts
H=$(sha256sum "$SCRIPT" | awk '{print $1}')

PHASE=preflight
SUCCESS=0
copy_receipts() {
  scp -q -F "$SSHCFG" -r "$R1:$R1RECV/." "$RECV/rank1-remote/" 2>/dev/null || \
    echo "WARN: could not copy rank-1 receipts; they remain on rank 1 at $R1RECV"
}
on_exit() {
  local rc=$?
  copy_receipts
  if [[ $SUCCESS -ne 1 ]]; then
    echo
    echo "ABORTED at phase=$PHASE (exit $rc). Hosts may be asymmetric."
    echo "  - read the last phase output above; do not start services until both hosts"
    echo "    are loaded and every cable is 20.0 Gb/s x 2 lanes;"
    echo "  - if the reset phase reported FATAL on a host, that host likely needs a"
    echo "    reboot; do not re-run the reset blindly;"
    echo "  - if no reset happened yet, the platform is still in its pre-reset state:"
    echo "    run 'load' on both, then the normal recovery and start."
  fi
}
trap on_exit EXIT

echo "== preflight (read-only) =="
for b in $BDFS; do
  p=/sys/bus/pci/devices/$b
  [[ -d $p ]] || { echo "FATAL rank0: $b missing" >&2; exit 1; }
  [[ "$(cat "$p/class")" == 0x0c0340 ]] || { echo "FATAL rank0: $b class $(cat "$p/class")" >&2; exit 1; }
  [[ "$(cat "$p/vendor")" == 0x1022 ]] || { echo "FATAL rank0: $b vendor $(cat "$p/vendor")" >&2; exit 1; }
  [[ " $(cat "$p/reset_method") " == *" acpi "* ]] || { echo "FATAL rank0: $b no acpi" >&2; exit 1; }
done
s0=$(systemctl is-active evox2-gufo@0.service || true)
[[ "$s0" == inactive || "$s0" == failed ]] || { echo "FATAL rank0 service=$s0; stop it first" >&2; exit 1; }
echo "rank0 ok (service=$s0)"
ssh -F "$SSHCFG" -T -o BatchMode=yes "$R1" '
  set -e
  s=$(systemctl is-active evox2-gufo@1.service || true); echo "rank1 service=$s"
  [[ "$s" == inactive || "$s" == failed ]] || { echo "FATAL rank1 service active"; exit 1; }
  for b in 0000:c7:00.5 0000:c7:00.6; do
    p=/sys/bus/pci/devices/$b
    [[ -d $p ]] || { echo "FATAL rank1: $b missing"; exit 1; }
    [[ "$(cat $p/class)" == 0x0c0340 ]] || { echo "FATAL rank1: $b class $(cat $p/class)"; exit 1; }
    [[ "$(cat $p/vendor)" == 0x1022 ]] || { echo "FATAL rank1: $b vendor $(cat $p/vendor)"; exit 1; }
    [[ " $(cat $p/reset_method) " == *" acpi "* ]] || { echo "FATAL rank1: $b no acpi"; exit 1; }
  done
  echo "rank1 ok"
'

echo "runner sha256 = $H"
scp -q -F "$SSHCFG" "$SCRIPT" "$R1:$R1SCRIPT"
chmod 700 "$R1DIR" 2>/dev/null || true
HR=$(ssh -F "$SSHCFG" -T -o BatchMode=yes "$R1" "sha256sum $R1SCRIPT" | awk '{print $1}')
[[ "$H" == "$HR" ]] || { echo "FATAL: runner hash differs on rank 1 ($HR)" >&2; exit 1; }
echo "rank 1 runner sha256 = $HR (match)"

echo
echo "This will, on BOTH hosts: stop Gufo, unload thunderbolt_ibverbs and thunderbolt,"
echo "ACPI-reset USB4 NHI functions 0000:c7:00.5 and 0000:c7:00.6, then reload thunderbolt."
read -r -p "Type RESET-NHI to proceed: " OK || { echo aborted; exit 1; }
[[ "$OK" == "RESET-NHI" ]] || { echo "aborted"; exit 1; }

for PHASE in unload reset load; do
  HR=$(ssh -F "$SSHCFG" -T -o BatchMode=yes "$R1" "sha256sum $R1SCRIPT" | awk '{print $1}')
  [[ "$HR" == "$H" ]] || { echo "FATAL: rank-1 runner changed before $PHASE ($HR)" >&2; exit 1; }
  echo
  echo "########## rank 0 : $PHASE ##########"
  sudo bash "$SCRIPT" "$PHASE" 0 "$RECV"
  echo "########## rank 1 : $PHASE (sudo password may be required) ##########"
  ssh -t -F "$SSHCFG" -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
      "$R1" "sudo bash $R1SCRIPT $PHASE 1 $R1RECV"
done

SUCCESS=1
echo
echo "RESET COMPLETE. Next: run the sanctioned startup (dashboard Start / paired"
echo "controller), then verify links/API and re-measure with the probe."
echo "See results/slowdown-20261024/NHI_RESET_RUNBOOK.md"
