#ifndef KILIX_ENCODEC_CONTENT_H
#define KILIX_ENCODEC_CONTENT_H

#include "kilix_encodec.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct kenc_installed_assets kenc_installed_assets;
typedef int (*kenc_admission_cancelled)(void *context);

/* Explicit installed admission, separate from the native byte-only loaders.
 * CONTENT=1 embeds an exact build-supplied kilix-content authority and helper.
 * A short owned system-Python process executes its sealed ZIP with isolated
 * imports, checks current packaged catalog/receipts/installed bytes, then
 * returns only sealed FDs. No path, environment or input audio supplies an
 * alternative catalog, helper, Python package or model identity.
 *
 * Profile is 1 (24k mono) or 2 (48k stereo), root is the absolute installed
 * content directory, timeout is 1..120000 ms. Cancellation is polled while
 * waiting; callbacks must return promptly and must not modify this context.
 * Successful assets own their FDs until freed. Native loaders borrow the set
 * below and repeat their compiled graph hashes/ORT checks. No readiness cache.
 * Builds without CONTENT=1 refuse KENC_ERR_MODEL and never use graph paths.
 */
kenc_result kenc_installed_assets_open(kenc_installed_assets **out,
    uint8_t profile, const char *root, uint32_t timeout_ms,
    kenc_admission_cancelled cancelled, void *context);
const kenc_asset_set *kenc_installed_assets_files(const kenc_installed_assets *assets);
void kenc_installed_assets_free(kenc_installed_assets *assets);

/* Build receipt identity, or NULL for a build without installed admission. */
const char *kenc_installed_content_commit(void);
const char *kenc_installed_bundle_sha256(void);

#ifdef __cplusplus
}
#endif
#endif
