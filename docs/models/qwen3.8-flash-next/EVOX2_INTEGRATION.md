# EVO-X2 integration candidate, 19 October 2026

Source parents:

- `neuhaus/feat/verbs-dmabuf-sendimm`: `eb976643533f7ff88a4a56d30d0a4fceb3298cee`
- `gufo-org/main`: `9e1afaaa4da0f36e3638ac88bf12b054d2a1114a`

The integration preserves TP partitions, DMA-BUF SEND_WITH_IMM negotiation,
host-memory fallback, paired prefill scratch, image-context serialization,
mirrored cache operations, and the greedy split MTP head. The loading HTTP
responder exists only on rank 0. Upstream vision preparation and structured
output changes remain included.

Conflict resolutions retain both rank-slice and compact-attention-list oracles.
Upstream pooled pinned allocations retain the larger whole-chunk staging and
two control records required by paired prefill. The deterministic F16 GEMM
route retains the TP rank's 48-row gate geometry. Q8 paired expert kernels
retain their staging rather than taking the Q4/Q5 wave-owned route.

Request-local draft vocabulary remains enabled only for single-host execution.
TP2 retains full-vocabulary split-head drafting until the vocabulary lifecycle
is qualified against the mirrored executor. This is a conservative integration
boundary, not a measured optimization. No numerical tolerance was relaxed.

Local Fedora CPU validation: 119 passed, seven model/media-dependent skips.
The three TP2 control/executor/instruction tests pass on both original and
candidate sources. The shared formatting check passes for 610 C++ files.
Detailed reports live in the isolated workspace's results directory.

GPU and two-host model qualification are separate requirements. In particular,
CPU tests do not establish near-full-context capacity, DMA-BUF correctness,
four-rail traffic, model quality, or performance. This candidate must not be
described as a tested deployment until that evidence exists. Historical tables
elsewhere in this directory retain their own source/hardware provenance.
