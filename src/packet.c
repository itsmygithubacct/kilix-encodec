#include "kilix_encodec.h"

_Static_assert(KENC_PACKET_SAMPLES == 960u,
    "the 24 kHz stream packet must contain 40 ms of PCM");
_Static_assert(
    (KENC_PACKET_FLAG_RESET | KENC_PACKET_FLAG_END
        | KENC_PACKET_FLAG_DISCONTINUITY)
        == UINT8_C(0x07),
    "packet flags must remain independent bits");

