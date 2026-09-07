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

/* Hash and load the same bytes. Never pass a mutable model pathname to ORT. */
static kenc_result read_verified(int directory, const char *name, size_t bytes,
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
    descriptor = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (descriptor < 0 || fstat(descriptor, &metadata) != 0
        || !S_ISREG(metadata.st_mode) || metadata.st_size < 0
        || (uintmax_t)metadata.st_size != bytes) {
        goto done;
    }
    data = malloc(bytes);
    if (data == NULL) { result = KENC_ERR_MEMORY; goto done; }
    while (offset < bytes) {
        ssize_t count = read(descriptor, data + offset, bytes - offset);
        if (count < 0 && errno == EINTR) { continue; }
        if (count <= 0) { goto done; }
        offset += (size_t)count;
    }
    unsigned char extra;
    ssize_t count;
    do { count = read(descriptor, &extra, 1u); } while (count < 0 && errno == EINTR);
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
    if (descriptor >= 0) { (void)close(descriptor); }
    free(data);
    return result;
}

kenc_result kenc_model_load(kenc_model **out, const char *asset_dir)
{
    kenc_model *model = NULL;
    int directory = -1;
    void *manifest = NULL;
    kenc_result result = KENC_ERR_MODEL;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (asset_dir == NULL || asset_dir[0] == '\0') { return KENC_ERR_INVALID; }
    directory = open(asset_dir, O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
    if (directory < 0) { return KENC_ERR_MODEL; }
    model = calloc(1u, sizeof(*model));
    if (model == NULL) { result = KENC_ERR_MEMORY; goto done; }
    atomic_init(&model->references, 1u);
    /* This is the exact scratch-export v2 contract, still user-supplied only. */
    result = read_verified(directory, "manifest.json", 12768u,
        "02201a5a947dc0a0b9cce84d585eca35fb8a7e57aee4d404d5cb14f495f40b6c",
        &manifest);
    if (result != KENC_OK) { goto done; }
    for (size_t i = 0u; i < KENC_GRAPH_COUNT; ++i) {
        result = read_verified(directory, kenc_graphs[i].file, kenc_graphs[i].bytes,
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
    (void)close(directory);
    kenc_model_free(model);
    return result;
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
}

kenc_result kenc_native_encode(kenc_native_stream *stream,
    const int16_t *pcm, uint16_t *codes)
{
    const OrtApi *api = stream->model->api;
    float *audio = stream->audio.data;
    const int64_t *tokens = stream->codes.data;
    kenc_result result;
    for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) { audio[i] = (float)pcm[i] / 32768.0f; }
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

#endif
