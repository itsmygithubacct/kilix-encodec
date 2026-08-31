#include "kilix_encodec.h"

#include <stdlib.h>

struct kenc_model {
    unsigned int reserved;
};

kenc_result kenc_model_load(kenc_model **out, const char *asset_dir)
{
    if (out == NULL) {
        return KENC_ERR_INVALID;
    }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0') {
        return KENC_ERR_INVALID;
    }

    /* P1 is deliberately fail-closed until the stateful ONNX gate lands. */
    return KENC_ERR_MODEL;
}

void kenc_model_free(kenc_model *model)
{
    free(model);
}

