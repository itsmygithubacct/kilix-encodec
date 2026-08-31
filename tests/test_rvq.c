#include "kilix_encodec.h"
#include "test.h"

int main(void)
{
    unsigned int passed = 0u;
    unsigned int total = 0u;
    kenc_options options = kenc_options_default();

    TEST_CHECK(KENC_CODEBOOK_CARDINALITY == 1024u);
    TEST_CHECK(options.codebooks == 8u);
    options.codebooks = 4u;
    TEST_CHECK(kenc_options_validate(&options) == KENC_OK);
    options.codebooks = 8u;
    TEST_CHECK(kenc_options_validate(&options) == KENC_OK);
    options.codebooks = 16u;
    TEST_CHECK(kenc_options_validate(&options) == KENC_OK);
    options.codebooks = 0u;
    TEST_CHECK(kenc_options_validate(&options) == KENC_ERR_INVALID);
    options.codebooks = 7u;
    TEST_CHECK(kenc_options_validate(&options) == KENC_ERR_INVALID);
    options.codebooks = UINT8_MAX;
    TEST_CHECK(kenc_options_validate(&options) == KENC_ERR_INVALID);

    return test_summary("test_rvq", passed, total);
}

