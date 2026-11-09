# Packet contract and epoch-start profiles

The KMA2 packet format used by live streams, and how peers agree on the C0 or C5-R4 epoch start. Moved from the README.

## Packet contract

KMA2 packets start with bytes `4b 4d 41 02`, followed by canonical unsigned
LEB128 integers for profile (1), epoch, packet index and PTS in milliseconds;
one flags byte; then canonical sample-count and payload-byte-count integers.
The payload stores 10-bit tokens most-significant-bit first, ordered by time
and then codebook. Codebooks (4, 8 or 16) are selected outside the packet by
the agreed stream profile. The parser verifies the exact derived payload
length, bounds every integer and refuses nonminimal encodings, trailing bytes,
unknown flags, out-of-range values and incompatible profiles.

Every regular encoder call consumes 960 samples (40 ms). The first packet and
each configured epoch boundary carry RESET; explicit encoder reset also marks
DISCONTINUITY. At every RESET packet both sides start from zeroed state, so an
epoch never depends on earlier packets and no latency is added. A decoder
refuses dependent packets after loss or reordering until it receives a later
RESET. Replayed epochs are refused. Short output buffers and malformed packets
do not write output.

## Epoch-start profiles and negotiation

Each stream has one epoch-start profile (owner decision OD-AT):

- **C0**: the zeroed state processes the RESET packet directly. This is the
  0.2.1 behaviour and the default of every new encoder and decoder, so a
  stream, peer or file without a marker is C0. Its packets are byte-identical
  to those of 3747330.
- **C5-R4** (owner decision OD-AL): the zeroed state is first primed by running
  the RESET packet's own input through the network four times as a discarded
  lead-in, which costs five network runs for that packet. Every RESET packet
  of a C5-R4 stream carries flag `0x08` (`KENC_PACKET_FLAG_EPOCH_PREROLL`).

Peers exchange `kenc_epoch_start_supported()` in their own handshake, then call
`kenc_epoch_start_negotiate(local, peer, &profile)`. It selects C5-R4 only when
both advertise it; a peer without an advertisement (0) gets C0, and a nonzero
advertisement without C0 is refused. Both sides then call
`kenc_encoder_set_epoch_start` or `kenc_decoder_set_epoch_start` before their
first packet, or after an explicit reset. A decoder refuses a RESET whose
marker differs from its profile with `KENC_ERR_EPOCH_START`, without output or
state change. A pre-marker decoder refuses the marked flag as a protocol error.
Mixed peers therefore either decode matching PCM or refuse; they never decode
silently different audio. Files carry the profile in format version 2
([FILE-FORMAT.md](../FILE-FORMAT.md)), and `kenc encode --epoch-start C5-R4`
writes one. `tools/epoch_stream.py` offers the same profiles, markers,
negotiation and file-header reading in Python.

The native tests exercise both profiles at all rates, independent streams,
reset recovery, asset substitution, epoch-start independence, negotiation,
marker refusal and profile selection rules. With `ORACLE=1` they compare exact
tokens and PCM against independent Python ORT sessions running
`tools/epoch_stream.py` with the same profile, including the programme's
syn-fixture item at 6 kb/s: tokens identical and PCM within 1 LSB, and the
Python renders equal the checked C5-R4 render and the 3747330 C0 reference.
`C0_REFERENCE_LIBRARY` names a library built from a git archive of 3747330.
C0 packets and PCM must then be byte-identical at all rates, each library must
decode the other's C0 stream with matching PCM, and 3747330 must refuse a
C5-R4 stream. Without it those 18 controls are printed and counted as SKIPPED.
