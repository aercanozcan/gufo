set -u
RANK=$1; HOST=$2; ROOT=$3; API=$4; BYTES=$5; ROUNDS=$6
IMG=sha256:224bc759a3f74d634e6804dfd909c1694bddcb49c7cb7280887dfb798dc1beac
{ sleep 40; echo release; } | podman --root "$ROOT/cache/containers" --runroot "$ROOT/cache/containers-run" run --rm -i --name "evox2-probe$RANK" \
 --network=host --security-opt label=disable --group-add keep-groups \
 --device=/dev/kfd --device=/dev/dri --device=/dev/infiniband --ulimit memlock=-1:-1 \
 -v "$ROOT/builds:/workspace/builds:ro" \
 -e LD_LIBRARY_PATH=/workspace/builds/rdma-core/lib \
 -e IBV_DRIVERS=/workspace/builds/rdma-core/lib/libusb4_rdma \
 "$IMG" /workspace/builds/gufo-communicator-probe/communicator-probe-normal \
 --rank "$RANK" --device 0 --rdma-device usb4_rdma0 --bootstrap-host "$HOST" \
 --expected-policy normal --api "$API" --bytes "$BYTES" --rounds "$ROUNDS"
echo "PROBE${RANK}_EXIT=$?"
