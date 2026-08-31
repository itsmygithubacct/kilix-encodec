#include "kilix_encodec.h"

_Static_assert(KENC_CODEBOOK_CARDINALITY == 1024u,
    "10-bit token packing requires 1,024-entry codebooks");
_Static_assert((1u << 10u) == KENC_CODEBOOK_CARDINALITY,
    "codebook cardinality must fit exactly in 10 bits");

