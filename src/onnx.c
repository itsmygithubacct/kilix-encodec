#define _POSIX_C_SOURCE 200809L
#include "internal.h"

#include <stdlib.h>

#ifndef KENC_WITH_ONNX

kenc_result kenc_model_load(kenc_model **out, const char *asset_dir)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0') { return KENC_ERR_INVALID; }
    return KENC_ERR_MODEL;
}
void kenc_model_free(kenc_model *model) { free(model); }
kenc_result kenc_model_load_fds(kenc_model **out, const kenc_asset_set *assets)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (assets == NULL || assets->files == NULL || assets->count != 9u) { return KENC_ERR_INVALID; }
    return KENC_ERR_MODEL;
}
kenc_result kenc_stereo_create(kenc_stereo **out, const char *asset_dir,
    uint8_t codebooks, uint8_t threads)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0'
        || (codebooks != 2u && codebooks != 4u && codebooks != 8u && codebooks != 16u)
        || (threads != 1u && threads != 2u)) { return KENC_ERR_INVALID; }
    return KENC_ERR_MODEL;
}
void kenc_stereo_free(kenc_stereo *codec) { free(codec); }
kenc_result kenc_stereo_create_fds(kenc_stereo **out, const kenc_asset_set *assets,
    uint8_t codebooks, uint8_t threads)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (assets == NULL || assets->files == NULL || assets->count != 4u
        || (codebooks != 2u && codebooks != 4u && codebooks != 8u && codebooks != 16u)
        || (threads != 1u && threads != 2u)) { return KENC_ERR_INVALID; }
    return KENC_ERR_MODEL;
}
kenc_result kenc_stereo_encode_frame(kenc_stereo *codec,
    const float *pcm, size_t sample_count, uint16_t *codes,
    size_t code_capacity, float *scale)
{
    (void)codec; (void)pcm; (void)sample_count; (void)codes;
    (void)code_capacity; (void)scale;
    return KENC_ERR_RUNTIME;
}
kenc_result kenc_stereo_decode_frame(kenc_stereo *codec,
    const uint16_t *codes, size_t code_count, float scale,
    float *pcm, size_t pcm_capacity)
{
    (void)codec; (void)codes; (void)code_count; (void)scale;
    (void)pcm; (void)pcm_capacity;
    return KENC_ERR_RUNTIME;
}

kenc_result kenc_native_create(kenc_native_stream **out, kenc_model *model,
    const kenc_options *options, int encoding)
{
    (void)model; (void)options; (void)encoding;
    *out = NULL;
    return KENC_ERR_RUNTIME;
}
void kenc_native_reset(kenc_native_stream *stream) { (void)stream; }
void kenc_native_free(kenc_native_stream *stream) { free(stream); }
kenc_result kenc_native_encode(kenc_native_stream *stream,
    const int16_t *pcm, uint16_t *codes)
{
    (void)stream; (void)pcm; (void)codes;
    return KENC_ERR_RUNTIME;
}
kenc_result kenc_native_decode(kenc_native_stream *stream,
    const uint16_t *codes, int16_t *pcm)
{
    (void)stream; (void)codes; (void)pcm;
    return KENC_ERR_RUNTIME;
}

#else

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <onnxruntime_c_api.h>
#include <openssl/evp.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "graph_contracts.h"

#define KENC_GRAPH_COUNT 8u
#define KENC_STATE_COUNT 12u

struct kenc_model {
    atomic_uint references;
    const OrtApi *api;
    OrtEnv *environment;
    void *graphs[KENC_GRAPH_COUNT];
};

typedef struct {
    void *data;
    size_t bytes;
    OrtValue *tensor;
} native_buffer;

struct kenc_native_stream {
    kenc_model *model;
    OrtSession *network;
    OrtSession *quantizer;
    OrtMemoryInfo *memory;
    native_buffer audio;
    native_buffer latent;
    native_buffer codes;
    native_buffer states[2][KENC_STATE_COUNT];
    OrtIoBinding *network_bindings[2];
    OrtIoBinding *quantizer_binding;
    unsigned int bank;
    uint8_t codebooks;
    int encoding;
    int preroll;
};

static kenc_result ort_result(const OrtApi *api, OrtStatus *status)
{
    if (status == NULL) { return KENC_OK; }
    api->ReleaseStatus(status);
    return KENC_ERR_RUNTIME;
}

#define ORT_TRY(expression) do { \
    result = ort_result(api, (expression)); \
    if (result != KENC_OK) { goto done; } \
} while (0)

typedef struct {
    int directory;
    const kenc_asset_set *assets;
} model_input;

static int valid_asset_set(const kenc_asset_set *assets, const char *const *names,
    size_t count)
{
    if (assets == NULL || assets->files == NULL || assets->count != count) { return 0; }
    for (size_t i = 0u; i < count; ++i) {
        const kenc_asset_fd *file = &assets->files[i];
        if (file->name == NULL || strnlen(file->name, 128u) == 128u || file->descriptor < 0) { return 0; }
        size_t matches = 0u;
        for (size_t j = 0u; j < count; ++j) {
            if (strcmp(file->name, names[j]) == 0) { ++matches; }
        }
        if (matches != 1u) { return 0; }
        for (size_t j = 0u; j < i; ++j) {
            if (strcmp(file->name, assets->files[j].name) == 0) { return 0; }
        }
    }
    return 1;
}

/* Hash and load the same bytes. Never pass a mutable model pathname to ORT.
 * Descriptor input is borrowed; pread never changes a caller's file offset. */
static kenc_result read_verified(const model_input *input, const char *name, size_t bytes,
    const char *expected, void **out)
{
    int descriptor = -1;
    struct stat metadata;
    unsigned char *data = NULL;
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_size = 0u;
    size_t offset = 0u;
    char hex[65];
    static const char digits[] = "0123456789abcdef";
    kenc_result result = KENC_ERR_MODEL;
    *out = NULL;
    if (input->assets == NULL) {
        descriptor = openat(input->directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    } else {
        for (size_t i = 0u; i < input->assets->count; ++i) {
            if (strcmp(input->assets->files[i].name, name) == 0) {
                descriptor = input->assets->files[i].descriptor;
                break;
            }
        }
    }
    if (descriptor < 0 || fstat(descriptor, &metadata) != 0
        || !S_ISREG(metadata.st_mode) || metadata.st_size < 0
        || (uintmax_t)metadata.st_size != bytes) {
        goto done;
    }
    if (input->assets != NULL) {
        int flags = fcntl(descriptor, F_GETFL);
        int descriptor_flags = fcntl(descriptor, F_GETFD);
        /* Stable Linux F_GET_SEALS UAPI, also for older libc headers. */
        int seals = fcntl(descriptor, 1034);
        if (metadata.st_uid != geteuid() || (metadata.st_mode & 0133) != 0
            || flags < 0 || (flags & O_ACCMODE) != O_RDONLY
            || descriptor_flags < 0 || (descriptor_flags & FD_CLOEXEC) == 0
            || seals < 0 || (seals & 0x000f) != 0x000f) { goto done; }
    }
    data = malloc(bytes);
    if (data == NULL) { result = KENC_ERR_MEMORY; goto done; }
    while (offset < bytes) {
        ssize_t count = pread(descriptor, data + offset, bytes - offset, (off_t)offset);
        if (count < 0 && errno == EINTR) { continue; }
        if (count <= 0) { goto done; }
        offset += (size_t)count;
    }
    unsigned char extra;
    ssize_t count;
    do { count = pread(descriptor, &extra, 1u, (off_t)bytes); } while (count < 0 && errno == EINTR);
    if (count != 0 || EVP_Digest(data, bytes, digest, &digest_size,
            EVP_sha256(), NULL) != 1 || digest_size != 32u) {
        goto done;
    }
    for (size_t i = 0u; i < 32u; ++i) {
        hex[i * 2u] = digits[digest[i] >> 4u];
        hex[i * 2u + 1u] = digits[digest[i] & 15u];
    }
    hex[64] = '\0';
    if (strcmp(hex, expected) != 0) { goto done; }
    *out = data;
    data = NULL;
    result = KENC_OK;
done:
    if (descriptor >= 0 && input->assets == NULL) { (void)close(descriptor); }
    free(data);
    return result;
}

static kenc_result model_load(kenc_model **out, const model_input *input)
{
    kenc_model *model = NULL;
    void *manifest = NULL;
    kenc_result result = KENC_ERR_MODEL;
    model = calloc(1u, sizeof(*model));
    if (model == NULL) { result = KENC_ERR_MEMORY; goto done; }
    atomic_init(&model->references, 1u);
    /* This is the exact scratch-export v2 contract, still user-supplied only. */
    result = read_verified(input, "manifest.json", 12768u,
        "02201a5a947dc0a0b9cce84d585eca35fb8a7e57aee4d404d5cb14f495f40b6c",
        &manifest);
    if (result != KENC_OK) { goto done; }
    for (size_t i = 0u; i < KENC_GRAPH_COUNT; ++i) {
        result = read_verified(input, kenc_graphs[i].file, kenc_graphs[i].bytes,
            kenc_graphs[i].sha256, &model->graphs[i]);
        if (result != KENC_OK) { goto done; }
    }
    model->api = OrtGetApiBase()->GetApi(21u);
    if (model->api == NULL) { result = KENC_ERR_RUNTIME; goto done; }
    result = ort_result(model->api, model->api->CreateEnv(
        ORT_LOGGING_LEVEL_WARNING, "kilix-encodec", &model->environment));
    if (result != KENC_OK) { goto done; }
    *out = model;
    model = NULL;
done:
    free(manifest);
    kenc_model_free(model);
    return result;
}

kenc_result kenc_model_load(kenc_model **out, const char *asset_dir)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0') { return KENC_ERR_INVALID; }
    int directory = open(asset_dir, O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
    if (directory < 0) { return KENC_ERR_MODEL; }
    const model_input input = {directory, NULL};
    kenc_result result = model_load(out, &input);
    (void)close(directory);
    return result;
}

kenc_result kenc_model_load_fds(kenc_model **out, const kenc_asset_set *assets)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    const char *names[KENC_GRAPH_COUNT + 1u];
    names[0] = "manifest.json";
    for (size_t i = 0u; i < KENC_GRAPH_COUNT; ++i) { names[i + 1u] = kenc_graphs[i].file; }
    if (!valid_asset_set(assets, names, KENC_GRAPH_COUNT + 1u)) { return KENC_ERR_INVALID; }
    const model_input input = {-1, assets};
    return model_load(out, &input);
}

void kenc_model_free(kenc_model *model)
{
    if (model == NULL) { return; }
    if (atomic_fetch_sub_explicit(&model->references, 1u, memory_order_acq_rel) != 1u) {
        return;
    }
    if (model->environment != NULL) { model->api->ReleaseEnv(model->environment); }
    for (size_t i = 0u; i < KENC_GRAPH_COUNT; ++i) { free(model->graphs[i]); }
    free(model);
}

static kenc_result check_tensor(const OrtApi *api, OrtSession *session,
    OrtAllocator *allocator, size_t index, int input,
    const kenc_tensor_contract *expected)
{
    char *name = NULL;
    OrtTypeInfo *info = NULL;
    const OrtTensorTypeAndShapeInfo *tensor = NULL;
    ONNXTensorElementDataType dtype;
    size_t rank = 0u;
    int64_t dims[3] = {0};
    kenc_result result = KENC_OK;
    ORT_TRY(input ? api->SessionGetInputName(session, index, allocator, &name)
                  : api->SessionGetOutputName(session, index, allocator, &name));
    if (name == NULL || strcmp(name, expected->name) != 0) {
        result = KENC_ERR_MODEL; goto done;
    }
    ORT_TRY(input ? api->SessionGetInputTypeInfo(session, index, &info)
                  : api->SessionGetOutputTypeInfo(session, index, &info));
    ORT_TRY(api->CastTypeInfoToTensorInfo(info, &tensor));
    if (tensor == NULL) { result = KENC_ERR_MODEL; goto done; }
    ORT_TRY(api->GetTensorElementType(tensor, &dtype));
    ORT_TRY(api->GetDimensionsCount(tensor, &rank));
    if (rank != expected->rank || rank > 3u || dtype !=
        (strcmp(name, "codes") == 0 ? ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64
                                   : ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)) {
        result = KENC_ERR_MODEL; goto done;
    }
    ORT_TRY(api->GetDimensions(tensor, dims, rank));
    if (memcmp(dims, expected->shape, rank * sizeof(*dims)) != 0) {
        result = KENC_ERR_MODEL;
    }
done:
    if (name != NULL) { allocator->Free(allocator, name); }
    if (info != NULL) { api->ReleaseTypeInfo(info); }
    return result;
}

static kenc_result create_session(kenc_model *model, size_t graph,
    uint8_t threads, OrtSession **out)
{
    const OrtApi *api = model->api;
    OrtSessionOptions *options = NULL;
    OrtAllocator *allocator = NULL;
    OrtSession *session = NULL;
    size_t inputs = 0u, outputs = 0u;
    kenc_result result = KENC_OK;
    ORT_TRY(api->CreateSessionOptions(&options));
    ORT_TRY(api->SetIntraOpNumThreads(options, (int)threads));
    ORT_TRY(api->SetInterOpNumThreads(options, 1));
    ORT_TRY(api->SetSessionExecutionMode(options, ORT_SEQUENTIAL));
    ORT_TRY(api->SetSessionGraphOptimizationLevel(options, ORT_ENABLE_ALL));
    ORT_TRY(api->AddSessionConfigEntry(options, "session.intra_op.allow_spinning", "0"));
    ORT_TRY(api->AddSessionConfigEntry(options, "session.inter_op.allow_spinning", "0"));
    ORT_TRY(api->CreateSessionFromArray(model->environment, model->graphs[graph],
        kenc_graphs[graph].bytes, options, &session));
    ORT_TRY(api->GetAllocatorWithDefaultOptions(&allocator));
    ORT_TRY(api->SessionGetInputCount(session, &inputs));
    ORT_TRY(api->SessionGetOutputCount(session, &outputs));
    if (inputs != kenc_graphs[graph].count || outputs != inputs) {
        result = KENC_ERR_MODEL; goto done;
    }
    for (size_t i = 0u; i < inputs; ++i) {
        result = check_tensor(api, session, allocator, i, 1, &kenc_graphs[graph].inputs[i]);
        if (result != KENC_OK) { goto done; }
        result = check_tensor(api, session, allocator, i, 0, &kenc_graphs[graph].outputs[i]);
        if (result != KENC_OK) { goto done; }
    }
    *out = session;
    session = NULL;
done:
    if (session != NULL) { api->ReleaseSession(session); }
    if (options != NULL) { api->ReleaseSessionOptions(options); }
    return result;
}

static kenc_result create_buffer(kenc_native_stream *stream, native_buffer *buffer,
    const kenc_tensor_contract *contract)
{
    ONNXTensorElementDataType dtype = strcmp(contract->name, "codes") == 0
        ? ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 : ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;
    size_t bytes = dtype == ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 ? sizeof(int64_t) : sizeof(float);
    for (size_t i = 0u; i < contract->rank; ++i) { bytes *= (size_t)contract->shape[i]; }
    /* Contract dimensions are compiled constants, never read from the graph. */
    buffer->data = calloc(1u, bytes);
    if (buffer->data == NULL) { return KENC_ERR_MEMORY; }
    buffer->bytes = bytes;
    return ort_result(stream->model->api,
        stream->model->api->CreateTensorWithDataAsOrtValue(stream->memory,
            buffer->data, bytes, contract->shape, contract->rank, dtype, &buffer->tensor));
}

static void free_buffer(const OrtApi *api, native_buffer *buffer)
{
    if (buffer->tensor != NULL) { api->ReleaseValue(buffer->tensor); }
    free(buffer->data);
}

void kenc_native_free(kenc_native_stream *stream)
{
    if (stream == NULL) { return; }
    const OrtApi *api = stream->model->api;
    for (size_t bank = 0u; bank < 2u; ++bank) {
        if (stream->network_bindings[bank] != NULL) { api->ReleaseIoBinding(stream->network_bindings[bank]); }
        for (size_t i = 0u; i < KENC_STATE_COUNT; ++i) { free_buffer(api, &stream->states[bank][i]); }
    }
    if (stream->quantizer_binding != NULL) { api->ReleaseIoBinding(stream->quantizer_binding); }
    free_buffer(api, &stream->audio);
    free_buffer(api, &stream->latent);
    free_buffer(api, &stream->codes);
    if (stream->network != NULL) { api->ReleaseSession(stream->network); }
    if (stream->quantizer != NULL) { api->ReleaseSession(stream->quantizer); }
    if (stream->memory != NULL) { api->ReleaseMemoryInfo(stream->memory); }
    kenc_model_free(stream->model);
    free(stream);
}

kenc_result kenc_native_create(kenc_native_stream **out, kenc_model *model,
    const kenc_options *options, int encoding)
{
    kenc_native_stream *stream = NULL;
    const OrtApi *api;
    size_t network_index = encoding ? 0u : 1u;
    size_t quantizer_index;
    kenc_result result;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    result = kenc_options_validate(options);
    if (result != KENC_OK) { return result; }
    if (model == NULL) { return KENC_ERR_MODEL; }
    quantizer_index = (options->codebooks == 4u ? 2u : options->codebooks == 8u ? 4u : 6u)
        + (encoding ? 0u : 1u);
    api = model->api;
    stream = calloc(1u, sizeof(*stream));
    if (stream == NULL) { return KENC_ERR_MEMORY; }
    stream->model = model;
    stream->encoding = encoding;
    stream->codebooks = options->codebooks;
    (void)atomic_fetch_add_explicit(&model->references, 1u, memory_order_relaxed);
    result = create_session(model, network_index, options->threads, &stream->network);
    if (result != KENC_OK) { goto done; }
    result = create_session(model, quantizer_index, options->threads, &stream->quantizer);
    if (result != KENC_OK) { goto done; }
    ORT_TRY(api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &stream->memory));
    result = create_buffer(stream, &stream->audio, &kenc_graphs[0].inputs[0]);
    if (result != KENC_OK) { goto done; }
    result = create_buffer(stream, &stream->latent, &kenc_graphs[0].outputs[0]);
    if (result != KENC_OK) { goto done; }
    result = create_buffer(stream, &stream->codes, encoding
        ? &kenc_graphs[quantizer_index].outputs[0] : &kenc_graphs[quantizer_index].inputs[0]);
    if (result != KENC_OK) { goto done; }
    for (size_t bank = 0u; bank < 2u; ++bank) {
        for (size_t state = 0u; state < KENC_STATE_COUNT; ++state) {
            result = create_buffer(stream, &stream->states[bank][state],
                &kenc_graphs[network_index].inputs[state + 1u]);
            if (result != KENC_OK) { goto done; }
        }
    }
    for (size_t bank = 0u; bank < 2u; ++bank) {
        OrtIoBinding *binding = NULL;
        ORT_TRY(api->CreateIoBinding(stream->network, &stream->network_bindings[bank]));
        binding = stream->network_bindings[bank];
        ORT_TRY(api->BindInput(binding, kenc_graphs[network_index].inputs[0].name,
            encoding ? stream->audio.tensor : stream->latent.tensor));
        ORT_TRY(api->BindOutput(binding, kenc_graphs[network_index].outputs[0].name,
            encoding ? stream->latent.tensor : stream->audio.tensor));
        for (size_t i = 0u; i < KENC_STATE_COUNT; ++i) {
            ORT_TRY(api->BindInput(binding, kenc_graphs[network_index].inputs[i + 1u].name,
                stream->states[bank][i].tensor));
            ORT_TRY(api->BindOutput(binding, kenc_graphs[network_index].outputs[i + 1u].name,
                stream->states[1u - bank][i].tensor));
        }
    }
    ORT_TRY(api->CreateIoBinding(stream->quantizer, &stream->quantizer_binding));
    ORT_TRY(api->BindInput(stream->quantizer_binding, kenc_graphs[quantizer_index].inputs[0].name,
        encoding ? stream->latent.tensor : stream->codes.tensor));
    ORT_TRY(api->BindOutput(stream->quantizer_binding, kenc_graphs[quantizer_index].outputs[0].name,
        encoding ? stream->codes.tensor : stream->latent.tensor));
    /* The stream start is an epoch start: calloc zeroed every state. */
    stream->preroll = 1;
    *out = stream;
    stream = NULL;
done:
    kenc_native_free(stream);
    return result;
}

void kenc_native_reset(kenc_native_stream *stream)
{
    if (stream == NULL) { return; }
    for (size_t bank = 0u; bank < 2u; ++bank) {
        for (size_t state = 0u; state < KENC_STATE_COUNT; ++state) {
            memset(stream->states[bank][state].data, 0, stream->states[bank][state].bytes);
        }
    }
    stream->bank = 0u;
    stream->preroll = 1;
}

/* Repeat pre-roll at an epoch start. The network input buffer already holds
 * the epoch's first packet (audio, or its RVQ-decoded codes), so running the
 * unchanged per-packet graph over it KENC_PREROLL_PACKETS times from zeroed
 * state is the tiled lead-in. Each lead-in output is overwritten by the next
 * run; no future packet is read, so no latency is added. A failure leaves the
 * flag set; callers reset the stream before it is used again. */
static kenc_result preroll(kenc_native_stream *stream)
{
    const OrtApi *api = stream->model->api;
    kenc_result result = KENC_OK;
    for (unsigned int i = 0u; i < KENC_PREROLL_PACKETS; ++i) {
        ORT_TRY(api->RunWithBinding(stream->network, NULL, stream->network_bindings[stream->bank]));
        stream->bank = 1u - stream->bank;
    }
    stream->preroll = 0;
done:
    return result;
}

kenc_result kenc_native_encode(kenc_native_stream *stream,
    const int16_t *pcm, uint16_t *codes)
{
    const OrtApi *api = stream->model->api;
    float *audio = stream->audio.data;
    const int64_t *tokens = stream->codes.data;
    kenc_result result;
    for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) { audio[i] = (float)pcm[i] / 32768.0f; }
    if (stream->preroll) {
        result = preroll(stream);
        if (result != KENC_OK) { goto done; }
    }
    ORT_TRY(api->RunWithBinding(stream->network, NULL, stream->network_bindings[stream->bank]));
    ORT_TRY(api->RunWithBinding(stream->quantizer, NULL, stream->quantizer_binding));
    for (size_t i = 0u; i < (size_t)stream->codebooks * KENC_LATENT_FRAMES; ++i) {
        if (tokens[i] < 0 || tokens[i] >= KENC_CODEBOOK_CARDINALITY) {
            result = KENC_ERR_RUNTIME; goto done;
        }
        codes[i] = (uint16_t)tokens[i];
    }
    stream->bank = 1u - stream->bank;
done:
    return result;
}

kenc_result kenc_native_decode(kenc_native_stream *stream,
    const uint16_t *codes, int16_t *pcm)
{
    const OrtApi *api = stream->model->api;
    const float *audio = stream->audio.data;
    int64_t *tokens = stream->codes.data;
    kenc_result result;
    for (size_t i = 0u; i < (size_t)stream->codebooks * KENC_LATENT_FRAMES; ++i) {
        if (codes[i] >= KENC_CODEBOOK_CARDINALITY) { return KENC_ERR_PROTOCOL; }
        tokens[i] = codes[i];
    }
    ORT_TRY(api->RunWithBinding(stream->quantizer, NULL, stream->quantizer_binding));
    if (stream->preroll) {
        result = preroll(stream);
        if (result != KENC_OK) { goto done; }
    }
    ORT_TRY(api->RunWithBinding(stream->network, NULL, stream->network_bindings[stream->bank]));
    for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) {
        if (!isfinite(audio[i])) { result = KENC_ERR_RUNTIME; goto done; }
    }
    for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) {
        float value = audio[i] * 32768.0f;
        if (value >= 32767.0f) { pcm[i] = INT16_MAX; }
        else if (value <= -32768.0f) { pcm[i] = INT16_MIN; }
        else { pcm[i] = (int16_t)lrintf(value); }
    }
    stream->bank = 1u - stream->bank;
done:
    return result;
}


/* The noncausal stereo profile has a separate context, contracts and loader.
 * It never shares causal state or a KMA2 profile ID with 24 kHz streaming. */
struct kenc_stereo {
    const OrtApi *api;
    OrtEnv *environment;
    OrtSession *encoder;
    OrtSession *decoder;
    OrtMemoryInfo *memory;
    OrtIoBinding *encode_binding;
    OrtIoBinding *decode_binding;
    native_buffer input;
    native_buffer latent;
    native_buffer scale;
    native_buffer output;
    float *books;
    float norms[16u * KENC_CODEBOOK_CARDINALITY];
    float residual[128u * KENC_STEREO_LATENT_FRAMES];
    uint16_t tokens[16u * KENC_STEREO_LATENT_FRAMES];
    uint8_t codebooks;
};

static const kenc_tensor_contract stereo_audio = { "audio", 3u, {1, 2, 48000} };
static const kenc_tensor_contract stereo_latent = { "latent", 3u, {1, 128, 150} };
static const kenc_tensor_contract stereo_quantized = { "quantized", 3u, {1, 128, 150} };
static const kenc_tensor_contract stereo_scale = { "scale", 2u, {1, 1, 0} };

static kenc_result stereo_session(kenc_stereo *codec, const void *graph,
    size_t graph_size, uint8_t threads, int encoding, OrtSession **out)
{
    const OrtApi *api = codec->api;
    OrtSessionOptions *options = NULL;
    OrtSession *session = NULL;
    OrtAllocator *allocator = NULL;
    size_t inputs = 0u, outputs = 0u;
    kenc_result result = KENC_OK;
    ORT_TRY(api->CreateSessionOptions(&options));
    ORT_TRY(api->SetIntraOpNumThreads(options, (int)threads));
    ORT_TRY(api->SetInterOpNumThreads(options, 1));
    ORT_TRY(api->SetSessionExecutionMode(options, ORT_SEQUENTIAL));
    ORT_TRY(api->SetSessionGraphOptimizationLevel(options, ORT_ENABLE_ALL));
    ORT_TRY(api->AddSessionConfigEntry(options, "session.intra_op.allow_spinning", "0"));
    ORT_TRY(api->AddSessionConfigEntry(options, "session.inter_op.allow_spinning", "0"));
    ORT_TRY(api->CreateSessionFromArray(codec->environment, graph, graph_size, options, &session));
    ORT_TRY(api->GetAllocatorWithDefaultOptions(&allocator));
    ORT_TRY(api->SessionGetInputCount(session, &inputs));
    ORT_TRY(api->SessionGetOutputCount(session, &outputs));
    if (inputs != (encoding ? 1u : 2u) || outputs != (encoding ? 2u : 1u)) {
        result = KENC_ERR_MODEL; goto done;
    }
    result = check_tensor(api, session, allocator, 0u, 1,
        encoding ? &stereo_audio : &stereo_quantized);
    if (result != KENC_OK) { goto done; }
    result = check_tensor(api, session, allocator, 0u, 0,
        encoding ? &stereo_latent : &stereo_audio);
    if (result != KENC_OK) { goto done; }
    result = check_tensor(api, session, allocator, 1u, !encoding, &stereo_scale);
    if (result != KENC_OK) { goto done; }
    *out = session;
    session = NULL;
done:
    if (session != NULL) { api->ReleaseSession(session); }
    if (options != NULL) { api->ReleaseSessionOptions(options); }
    return result;
}

static kenc_result stereo_buffer(kenc_stereo *codec, native_buffer *buffer,
    const kenc_tensor_contract *contract)
{
    size_t bytes = sizeof(float);
    for (size_t i = 0u; i < contract->rank; ++i) { bytes *= (size_t)contract->shape[i]; }
    buffer->data = calloc(1u, bytes);
    if (buffer->data == NULL) { return KENC_ERR_MEMORY; }
    buffer->bytes = bytes;
    return ort_result(codec->api, codec->api->CreateTensorWithDataAsOrtValue(
        codec->memory, buffer->data, bytes, contract->shape, contract->rank,
        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &buffer->tensor));
}

void kenc_stereo_free(kenc_stereo *codec)
{
    if (codec == NULL) { return; }
    const OrtApi *api = codec->api;
    if (codec->encode_binding != NULL) { api->ReleaseIoBinding(codec->encode_binding); }
    if (codec->decode_binding != NULL) { api->ReleaseIoBinding(codec->decode_binding); }
    free_buffer(api, &codec->input);
    free_buffer(api, &codec->output);
    free_buffer(api, &codec->latent);
    free_buffer(api, &codec->scale);
    if (codec->encoder != NULL) { api->ReleaseSession(codec->encoder); }
    if (codec->decoder != NULL) { api->ReleaseSession(codec->decoder); }
    if (codec->memory != NULL) { api->ReleaseMemoryInfo(codec->memory); }
    if (codec->environment != NULL) { api->ReleaseEnv(codec->environment); }
    free(codec->books);
    free(codec);
}

static kenc_result stereo_create(kenc_stereo **out, const model_input *input,
    uint8_t codebooks, uint8_t threads)
{
    static const char *const files[4] = {
        "manifest.json", "encoder_frame_op17.onnx", "decoder_frame_op17.onnx", "rvq-codebooks.f32le"
    };
    static const size_t sizes[4] = {3901u, 29909926u, 29882042u, 8388608u};
    static const char *const hashes[4] = {
        "844d8fcfdb2fb13d0485a9429c83debb590dcf964244fe0d06c50b5ec3380e38",
        "2fad822a1ab98a9b7d83340121c7cd7c8bb0a6cb2457b70aa458e6ef9022e27a",
        "e0c2bc574a50e910f7d0daa7c1598c237cec0a4365eb229ecfbaf9c06dd397b1",
        "4304cd8e3c8a9b59733224311aa405e0b04bd9b6c6737c32d8f682f9b255594f"
    };
    kenc_stereo *codec = NULL;
    const OrtApi *api = NULL;
    void *data[4] = {NULL};
    kenc_result result = KENC_ERR_MODEL;
    if (input->assets != NULL && !valid_asset_set(input->assets, files, 4u)) { return KENC_ERR_INVALID; }
    for (size_t i = 0u; i < 4u; ++i) {
        result = read_verified(input, files[i], sizes[i], hashes[i], &data[i]);
        if (result != KENC_OK) { goto done; }
    }
    codec = calloc(1u, sizeof(*codec));
    if (codec == NULL) { result = KENC_ERR_MEMORY; goto done; }
    api = OrtGetApiBase()->GetApi(21u);
    if (api == NULL) { result = KENC_ERR_RUNTIME; goto done; }
    codec->api = api;
    codec->codebooks = codebooks;
    codec->books = data[3];
    data[3] = NULL;
    /* Decode little-endian IEEE float32 bytes in place, independently of the
     * host byte order. Only already hash-verified codebooks reach this step. */
    _Static_assert(sizeof(float) == 4u, "the stereo profile requires float32");
    for (size_t i = 0u; i < sizes[3] / 4u; ++i) {
        const unsigned char *bytes = (const unsigned char *)codec->books + i * 4u;
        uint32_t bits = (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8u
            | (uint32_t)bytes[2] << 16u | (uint32_t)bytes[3] << 24u;
        memcpy(&codec->books[i], &bits, 4u);
        if (!isfinite(codec->books[i])) { result = KENC_ERR_MODEL; goto done; }
        codec->norms[i / 128u] += codec->books[i] * codec->books[i];
    }
    ORT_TRY(api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "kilix-encodec-stereo", &codec->environment));
    result = stereo_session(codec, data[1], sizes[1], threads, 1, &codec->encoder);
    if (result != KENC_OK) { goto done; }
    result = stereo_session(codec, data[2], sizes[2], threads, 0, &codec->decoder);
    if (result != KENC_OK) { goto done; }
    ORT_TRY(api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &codec->memory));
    native_buffer *buffers[] = {&codec->input, &codec->output, &codec->latent, &codec->scale};
    const kenc_tensor_contract *contracts[] = {&stereo_audio, &stereo_audio, &stereo_latent, &stereo_scale};
    for (size_t i = 0u; i < 4u; ++i) {
        result = stereo_buffer(codec, buffers[i], contracts[i]);
        if (result != KENC_OK) { goto done; }
    }
    ORT_TRY(api->CreateIoBinding(codec->encoder, &codec->encode_binding));
    ORT_TRY(api->BindInput(codec->encode_binding, "audio", codec->input.tensor));
    ORT_TRY(api->BindOutput(codec->encode_binding, "latent", codec->latent.tensor));
    ORT_TRY(api->BindOutput(codec->encode_binding, "scale", codec->scale.tensor));
    ORT_TRY(api->CreateIoBinding(codec->decoder, &codec->decode_binding));
    ORT_TRY(api->BindInput(codec->decode_binding, "quantized", codec->latent.tensor));
    ORT_TRY(api->BindInput(codec->decode_binding, "scale", codec->scale.tensor));
    ORT_TRY(api->BindOutput(codec->decode_binding, "audio", codec->output.tensor));
    *out = codec;
    codec = NULL;
done:
    for (size_t i = 0u; i < 4u; ++i) { free(data[i]); }
    kenc_stereo_free(codec);
    return result;
}

kenc_result kenc_stereo_create(kenc_stereo **out, const char *asset_dir,
    uint8_t codebooks, uint8_t threads)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0'
        || (codebooks != 2u && codebooks != 4u && codebooks != 8u && codebooks != 16u)
        || (threads != 1u && threads != 2u)) { return KENC_ERR_INVALID; }
    int directory = open(asset_dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) { return KENC_ERR_MODEL; }
    const model_input input = {directory, NULL};
    kenc_result result = stereo_create(out, &input, codebooks, threads);
    (void)close(directory);
    return result;
}

kenc_result kenc_stereo_create_fds(kenc_stereo **out, const kenc_asset_set *assets,
    uint8_t codebooks, uint8_t threads)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (assets == NULL || assets->files == NULL || assets->count != 4u
        || (codebooks != 2u && codebooks != 4u && codebooks != 8u && codebooks != 16u)
        || (threads != 1u && threads != 2u)) { return KENC_ERR_INVALID; }
    const model_input input = {-1, assets};
    return stereo_create(out, &input, codebooks, threads);
}

kenc_result kenc_stereo_encode_frame(kenc_stereo *codec,
    const float *pcm, size_t sample_count, uint16_t *codes,
    size_t code_capacity, float *scale)
{
    if (codec == NULL || pcm == NULL || codes == NULL || scale == NULL
        || sample_count != KENC_STEREO_FRAME_SAMPLES) { return KENC_ERR_INVALID; }
    size_t count = (size_t)codec->codebooks * KENC_STEREO_LATENT_FRAMES;
    if (code_capacity < count) { return KENC_ERR_TRUNCATED; }
    for (size_t i = 0u; i < KENC_STEREO_FRAME_SAMPLES * 2u; ++i) {
        if (!isfinite(pcm[i]) || pcm[i] < -1.0f || pcm[i] > 1.0f) { return KENC_ERR_INVALID; }
    }
    float *input = codec->input.data;
    for (size_t i = 0u; i < KENC_STEREO_FRAME_SAMPLES; ++i) {
        input[i] = pcm[i * 2u];
        input[KENC_STEREO_FRAME_SAMPLES + i] = pcm[i * 2u + 1u];
    }
    kenc_result result = ort_result(codec->api,
        codec->api->RunWithBinding(codec->encoder, NULL, codec->encode_binding));
    if (result != KENC_OK) { return result; }
    float value = *(float *)codec->scale.data;
    if (!isfinite(value) || value <= 0.0f || value > 2.0f) { return KENC_ERR_RUNTIME; }
    result = kenc_rvq_encode_frame(codec->latent.data, codec->books, codec->norms,
        codec->codebooks, codec->residual, codec->tokens);
    if (result != KENC_OK) { return result; }
    memcpy(codes, codec->tokens, count * sizeof(*codes));
    *scale = value;
    return KENC_OK;
}

kenc_result kenc_stereo_decode_frame(kenc_stereo *codec,
    const uint16_t *codes, size_t code_count, float scale,
    float *pcm, size_t pcm_capacity)
{
    if (codec == NULL || codes == NULL || pcm == NULL
        || code_count != (size_t)codec->codebooks * KENC_STEREO_LATENT_FRAMES) { return KENC_ERR_INVALID; }
    if (pcm_capacity < KENC_STEREO_FRAME_SAMPLES * 2u) { return KENC_ERR_TRUNCATED; }
    if (!isfinite(scale) || scale <= 0.0f || scale > 2.0f) { return KENC_ERR_PROTOCOL; }
    for (size_t i = 0u; i < code_count; ++i) {
        if (codes[i] >= KENC_CODEBOOK_CARDINALITY) { return KENC_ERR_PROTOCOL; }
    }
    kenc_rvq_decode_frame(codes, codec->books, codec->codebooks, codec->latent.data);
    *(float *)codec->scale.data = scale;
    kenc_result result = ort_result(codec->api,
        codec->api->RunWithBinding(codec->decoder, NULL, codec->decode_binding));
    if (result != KENC_OK) { return result; }
    const float *output = codec->output.data;
    for (size_t i = 0u; i < KENC_STEREO_FRAME_SAMPLES * 2u; ++i) {
        if (!isfinite(output[i])) { return KENC_ERR_RUNTIME; }
    }
    for (size_t i = 0u; i < KENC_STEREO_FRAME_SAMPLES; ++i) {
        pcm[i * 2u] = output[i];
        pcm[i * 2u + 1u] = output[KENC_STEREO_FRAME_SAMPLES + i];
    }
    return KENC_OK;
}

#endif
