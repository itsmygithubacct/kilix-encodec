/* Exact graph contracts emitted by the locked 0.1.5 exporter. No model payload. */
#ifndef KENC_GRAPH_CONTRACTS_H
#define KENC_GRAPH_CONTRACTS_H

typedef struct {
    const char *name;
    size_t rank;
    int64_t shape[3];
} kenc_tensor_contract;

typedef struct {
    const char *file;
    size_t bytes;
    const char *sha256;
    size_t count;
    kenc_tensor_contract inputs[13];
    kenc_tensor_contract outputs[13];
} kenc_graph_contract;

static const kenc_graph_contract kenc_graphs[8] = {
    {
        "encoder_stateful_op17.onnx", 29720617u,
        "19c6f57f0d39d2660942c958a797133fa0eedd4120cccf5daecc5726427b8269", 13u,
        {
            { "audio", 3u, { 1, 1, 960 } },
            { "state_in_encoder_model_0_context", 3u, { 1, 1, 6 } },
            { "state_in_encoder_model_1_block_1_context", 3u, { 1, 32, 2 } },
            { "state_in_encoder_model_3_context", 3u, { 1, 32, 2 } },
            { "state_in_encoder_model_4_block_1_context", 3u, { 1, 64, 2 } },
            { "state_in_encoder_model_6_context", 3u, { 1, 64, 4 } },
            { "state_in_encoder_model_7_block_1_context", 3u, { 1, 128, 2 } },
            { "state_in_encoder_model_9_context", 3u, { 1, 128, 5 } },
            { "state_in_encoder_model_10_block_1_context", 3u, { 1, 256, 2 } },
            { "state_in_encoder_model_12_context", 3u, { 1, 256, 8 } },
            { "state_in_encoder_model_13_hidden", 3u, { 2, 1, 512 } },
            { "state_in_encoder_model_13_cell", 3u, { 2, 1, 512 } },
            { "state_in_encoder_model_15_context", 3u, { 1, 512, 6 } },
        },
        {
            { "latent", 3u, { 1, 128, 3 } },
            { "state_out_encoder_model_0_context", 3u, { 1, 1, 6 } },
            { "state_out_encoder_model_1_block_1_context", 3u, { 1, 32, 2 } },
            { "state_out_encoder_model_3_context", 3u, { 1, 32, 2 } },
            { "state_out_encoder_model_4_block_1_context", 3u, { 1, 64, 2 } },
            { "state_out_encoder_model_6_context", 3u, { 1, 64, 4 } },
            { "state_out_encoder_model_7_block_1_context", 3u, { 1, 128, 2 } },
            { "state_out_encoder_model_9_context", 3u, { 1, 128, 5 } },
            { "state_out_encoder_model_10_block_1_context", 3u, { 1, 256, 2 } },
            { "state_out_encoder_model_12_context", 3u, { 1, 256, 8 } },
            { "state_out_encoder_model_13_hidden", 3u, { 2, 1, 512 } },
            { "state_out_encoder_model_13_cell", 3u, { 2, 1, 512 } },
            { "state_out_encoder_model_15_context", 3u, { 1, 512, 6 } },
        },
    },
    {
        "decoder_stateful_op17.onnx", 29733075u,
        "90cb4f26f05df4ad3537cfc2ab495fa4e6f9074b5f32303cdf4b89dbe548722a", 13u,
        {
            { "quantized", 3u, { 1, 128, 3 } },
            { "state_in_decoder_model_0_context", 3u, { 1, 128, 6 } },
            { "state_in_decoder_model_1_hidden", 3u, { 2, 1, 512 } },
            { "state_in_decoder_model_1_cell", 3u, { 2, 1, 512 } },
            { "state_in_decoder_model_3_overlap", 3u, { 1, 256, 8 } },
            { "state_in_decoder_model_4_block_1_context", 3u, { 1, 256, 2 } },
            { "state_in_decoder_model_6_overlap", 3u, { 1, 128, 5 } },
            { "state_in_decoder_model_7_block_1_context", 3u, { 1, 128, 2 } },
            { "state_in_decoder_model_9_overlap", 3u, { 1, 64, 4 } },
            { "state_in_decoder_model_10_block_1_context", 3u, { 1, 64, 2 } },
            { "state_in_decoder_model_12_overlap", 3u, { 1, 32, 2 } },
            { "state_in_decoder_model_13_block_1_context", 3u, { 1, 32, 2 } },
            { "state_in_decoder_model_15_context", 3u, { 1, 32, 6 } },
        },
        {
            { "audio", 3u, { 1, 1, 960 } },
            { "state_out_decoder_model_0_context", 3u, { 1, 128, 6 } },
            { "state_out_decoder_model_1_hidden", 3u, { 2, 1, 512 } },
            { "state_out_decoder_model_1_cell", 3u, { 2, 1, 512 } },
            { "state_out_decoder_model_3_overlap", 3u, { 1, 256, 8 } },
            { "state_out_decoder_model_4_block_1_context", 3u, { 1, 256, 2 } },
            { "state_out_decoder_model_6_overlap", 3u, { 1, 128, 5 } },
            { "state_out_decoder_model_7_block_1_context", 3u, { 1, 128, 2 } },
            { "state_out_decoder_model_9_overlap", 3u, { 1, 64, 4 } },
            { "state_out_decoder_model_10_block_1_context", 3u, { 1, 64, 2 } },
            { "state_out_decoder_model_12_overlap", 3u, { 1, 32, 2 } },
            { "state_out_decoder_model_13_block_1_context", 3u, { 1, 32, 2 } },
            { "state_out_decoder_model_15_context", 3u, { 1, 32, 6 } },
        },
    },
    {
        "rvq_encode_3kbps_op17.onnx", 3677949u,
        "a873a3264a134df0d61557e0e751ac3702f15ad5a8fb97d53712ab746d533ad0", 1u,
        {
            { "latent", 3u, { 1, 128, 3 } },
        },
        {
            { "codes", 3u, { 4, 1, 3 } },
        },
    },
    {
        "rvq_decode_3kbps_op17.onnx", 2099386u,
        "776ab1b5a3990fb9b96a6eca43010356b08dc6f9c423af32601fb67f84a8a322", 1u,
        {
            { "codes", 3u, { 4, 1, 3 } },
        },
        {
            { "quantized", 3u, { 1, 128, 3 } },
        },
    },
    {
        "rvq_encode_6kbps_op17.onnx", 7880428u,
        "938a494c90dcb185a01e18457a9c08d6b6184785e8099baca29499cf5dbd020c", 1u,
        {
            { "latent", 3u, { 1, 128, 3 } },
        },
        {
            { "codes", 3u, { 8, 1, 3 } },
        },
    },
    {
        "rvq_decode_6kbps_op17.onnx", 4198507u,
        "505b6e01f75e6e966499be4e20a42d282959320653978a951cf44094aae7b2c0", 1u,
        {
            { "codes", 3u, { 8, 1, 3 } },
        },
        {
            { "quantized", 3u, { 1, 128, 3 } },
        },
    },
    {
        "rvq_encode_12kbps_op17.onnx", 16285603u,
        "cf1c80c23955255809ba90f564e6f30e0911851a298c4622f180585b4a4c5a37", 1u,
        {
            { "latent", 3u, { 1, 128, 3 } },
        },
        {
            { "codes", 3u, { 16, 1, 3 } },
        },
    },
    {
        "rvq_decode_12kbps_op17.onnx", 8396869u,
        "5e0db494814c629326fe4d82ce799915fdb45f841d3d99526bf126468849b9b2", 1u,
        {
            { "codes", 3u, { 16, 1, 3 } },
        },
        {
            { "quantized", 3u, { 1, 128, 3 } },
        },
    },
};

#endif
