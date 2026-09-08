"""Exact installed graph identities; no model payload or local paths."""

PROFILES = {
    1: ("encodec-24khz-stateful", "op17-v2-02201a5a", 134217728, (
        ("manifest.json", 12768, "02201a5a947dc0a0b9cce84d585eca35fb8a7e57aee4d404d5cb14f495f40b6c"),
        ("encoder_stateful_op17.onnx", 29720617, "19c6f57f0d39d2660942c958a797133fa0eedd4120cccf5daecc5726427b8269"),
        ("decoder_stateful_op17.onnx", 29733075, "90cb4f26f05df4ad3537cfc2ab495fa4e6f9074b5f32303cdf4b89dbe548722a"),
        ("rvq_encode_3kbps_op17.onnx", 3677949, "a873a3264a134df0d61557e0e751ac3702f15ad5a8fb97d53712ab746d533ad0"),
        ("rvq_decode_3kbps_op17.onnx", 2099386, "776ab1b5a3990fb9b96a6eca43010356b08dc6f9c423af32601fb67f84a8a322"),
        ("rvq_encode_6kbps_op17.onnx", 7880428, "938a494c90dcb185a01e18457a9c08d6b6184785e8099baca29499cf5dbd020c"),
        ("rvq_decode_6kbps_op17.onnx", 4198507, "505b6e01f75e6e966499be4e20a42d282959320653978a951cf44094aae7b2c0"),
        ("rvq_encode_12kbps_op17.onnx", 16285603, "cf1c80c23955255809ba90f564e6f30e0911851a298c4622f180585b4a4c5a37"),
        ("rvq_decode_12kbps_op17.onnx", 8396869, "5e0db494814c629326fe4d82ce799915fdb45f841d3d99526bf126468849b9b2"),
    )),
    2: ("encodec-48khz-frame", "op17-v1-844d8fcf", 100663296, (
        ("manifest.json", 3901, "844d8fcfdb2fb13d0485a9429c83debb590dcf964244fe0d06c50b5ec3380e38"),
        ("encoder_frame_op17.onnx", 29909926, "2fad822a1ab98a9b7d83340121c7cd7c8bb0a6cb2457b70aa458e6ef9022e27a"),
        ("decoder_frame_op17.onnx", 29882042, "e0c2bc574a50e910f7d0daa7c1598c237cec0a4365eb229ecfbaf9c06dd397b1"),
        ("rvq-codebooks.f32le", 8388608, "4304cd8e3c8a9b59733224311aa405e0b04bd9b6c6737c32d8f682f9b255594f"),
    )),
}
