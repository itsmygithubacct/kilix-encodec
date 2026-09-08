#include "kilix_encodec_content.h"
#include "test.h"

static unsigned int passed = 0u, total = 0u;

static int canceled(void *context) { (void)context; return 1; }

int main(void)
{
    kenc_installed_assets *assets = (kenc_installed_assets *)(uintptr_t)1u;
    TEST_CHECK(kenc_installed_assets_open(NULL, 1u, "/", 1000u, NULL, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_installed_assets_open(&assets, 0u, "/", 1000u, NULL, NULL) == KENC_ERR_INVALID && assets == NULL);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, NULL, 1000u, NULL, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, "relative", 1000u, NULL, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, "/", 0u, NULL, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, "/", 120001u, NULL, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_installed_assets_files(NULL) == NULL);
    kenc_installed_assets_free(NULL);
#ifdef KENC_WITH_CONTENT
    TEST_CHECK(kenc_installed_content_commit() != NULL);
    TEST_CHECK(kenc_installed_bundle_sha256() != NULL);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, "/", 1000u, canceled, NULL) == KENC_ERR_RUNTIME);
#else
    TEST_CHECK(kenc_installed_content_commit() == NULL);
    TEST_CHECK(kenc_installed_bundle_sha256() == NULL);
    TEST_CHECK(kenc_installed_assets_open(&assets, 1u, "/", 1000u, canceled, NULL) == KENC_ERR_MODEL);
#endif
    return test_summary("installed_admission", passed, total);
}
