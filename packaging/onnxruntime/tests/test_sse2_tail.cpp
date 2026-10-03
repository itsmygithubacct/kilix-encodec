#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <xmmintrin.h>
#define MLAS_TARGET_AMD64
#include "narrow-kernel.inc"  // Extracted from the patched ORT source by run.py.

extern "C" size_t MlasGemmFloatKernelSse(const float*, const float*, float*,
    size_t, size_t, size_t, size_t, size_t, float, bool);
extern "C" size_t MlasGemmFloatKernelSsePatched(const float*, const float*, float*,
    size_t, size_t, size_t, size_t, size_t, float, bool);

int main() {
    uint32_t seed = 0x65804a31;
    auto value = [&]() {
        seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
        return float(int32_t(seed % 20001) - 10000) / 4096.0f;
    };
    size_t checked = 0;
    for (size_t m = 1; m <= 17; ++m) {
        for (size_t n = 1; n <= 40; ++n) {
            for (size_t k : {1u, 2u, 3u, 4u, 7u, 15u, 16u, 31u, 255u, 256u, 257u, 1024u}) {
                const size_t lda = k + 3, ldc = n + 5;
                std::vector<float> a(m * lda), b(k * ((n + 15) / 16) * 16);
                for (auto& f : a) f = value();
                for (auto& f : b) f = value();
                for (float alpha : {0.0f, 1.0f, -1.0f, 0.25f, 1.25f}) {
                    for (bool zero : {false, true}) {
                        std::vector<float> reference(m * ldc + 8), actual;
                        for (auto& f : reference) f = value();
                        actual = reference;
                        size_t row = 0;
                        while (row < m) row += MlasGemmFloatKernelSse(
                            a.data() + row * lda, b.data(), reference.data() + row * ldc,
                            k, m - row, n, lda, ldc, alpha, zero);
                        const size_t tail = n % 16;
                        if (tail != 0 && tail <= 8) {
                            const size_t full = n - tail;
                            row = 0;
                            if (full) while (row < m) row += MlasGemmFloatKernelSsePatched(
                                a.data() + row * lda, b.data(), actual.data() + row * ldc,
                                k, m - row, full, lda, ldc, alpha, zero);
                            row = 0;
                            while (row < m) row += MlasSgemmNarrowKernelSse(
                                a.data() + row * lda, b.data() + full * k, actual.data() + row * ldc + full,
                                k, m - row, tail, lda, ldc, alpha, zero);
                        } else {
                            row = 0;
                            while (row < m) row += MlasGemmFloatKernelSsePatched(
                                a.data() + row * lda, b.data(), actual.data() + row * ldc,
                                k, m - row, n, lda, ldc, alpha, zero);
                        }
                        if (std::memcmp(reference.data(), actual.data(), actual.size() * sizeof(float))) {
                            std::fprintf(stderr, "Mismatch M=%zu N=%zu K=%zu alpha=%g zero=%d\n",
                                         m, n, k, alpha, zero);
                            return 1;
                        }
                        ++checked;
                    }
                }
            }
        }
    }
    std::printf("PASS: %zu exact SSE2 kernel comparisons including output guards\n", checked);
}
