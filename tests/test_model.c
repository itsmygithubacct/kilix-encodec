#include "kilix_encodec.h"
#include "test.h"

#include <string.h>

int main(void)
{
    unsigned int passed = 0u;
    unsigned int total = 0u;
    kenc_model *model = (kenc_model *)(uintptr_t)1u;
    kenc_options options = kenc_options_default();

    TEST_CHECK(kenc_model_load(NULL, "missing") == KENC_ERR_INVALID);
    TEST_CHECK(kenc_model_load(&model, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(model == NULL);
    model = (kenc_model *)(uintptr_t)1u;
    TEST_CHECK(kenc_model_load(&model, "") == KENC_ERR_INVALID);
    TEST_CHECK(model == NULL);
    model = (kenc_model *)(uintptr_t)1u;
    TEST_CHECK(kenc_model_load(&model, "missing") == KENC_ERR_MODEL);
    TEST_CHECK(model == NULL);
    TEST_CHECK(kenc_options_validate(NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_options_validate(&options) == KENC_OK);
    TEST_CHECK(strcmp(kenc_result_string(KENC_ERR_MODEL),
                   "model unavailable or invalid")
        == 0);
    TEST_CHECK(strcmp(kenc_result_string((kenc_result)999), "unknown result")
        == 0);
    kenc_model_free(NULL);
    TEST_CHECK(1);

    return test_summary("test_model", passed, total);
}
