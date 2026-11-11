#include "prx/libSceAgcDriver/Execution/include/VulkanDevice.hpp"
#include "Recompiler.hpp"
#include "VulkanTestDevice.hpp"
#include <spirv/unified1/spirv.hpp>
#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <random>
#include <string>
#include <vector>

namespace {

using ShaderRecompiler::ShaderStage;

constexpr std::uint32_t Threads = 32;
constexpr std::uint32_t Stride = 4;
alignas(256) std::array<std::uint32_t, Threads * Stride> Input{};
alignas(256) std::array<std::uint32_t, Threads * Stride> Output{};

constexpr std::array<std::uint32_t, 20> Macro{
    0xd56d1408u, 0x04120b05u, 0xd56d6a09u, 0x04120b04u, 0x7e145508u, 0xbf800000u, 0xd54b000bu, 0x23ca1508u,
    0x5614150bu, 0x10161509u, 0xd54b000cu, 0x24261708u, 0x5616150cu, 0xd54b0008u, 0x24261708u, 0xbf800000u,
    0xd56f0008u, 0x042e1508u, 0xd55f000au, 0x04120b08u,
};

std::vector<std::uint32_t> Assemble(std::uint32_t count, std::uint32_t salt) {
    std::vector<std::uint32_t> code{0x34020082u, 0xe0302000u, 0x80000401u, 0xe0302004u, 0x80000501u, 0xbf8c3f70u, 0x7e1a02ffu, salt};
    for (std::uint32_t index = 0; index < count; ++index) {
        code.insert(code.end(), Macro.begin(), Macro.end());
        code.push_back(0x7e0a030au);
    }
    code.insert(code.end(), {0xe0702000u, 0x80010501u, 0xe0702004u, 0x80010d01u, 0xbf810000u});
    return code;
}

std::array<std::uint32_t, 4> BufferDescriptor(const void* data, std::uint32_t count) {
    const auto address = reinterpret_cast<std::uintptr_t>(data);
    return {static_cast<std::uint32_t>(address), static_cast<std::uint32_t>((address >> 32u) & 0xffffu) | (4u << 16u), count, 0x11016facu};
}

double Milliseconds(std::chrono::steady_clock::time_point since) {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - since).count();
}

std::uint32_t Env(const char* name, std::uint32_t fallback) {
    const char* text = std::getenv(name);
    return text == nullptr ? fallback : static_cast<std::uint32_t>(std::strtoul(text, nullptr, 0));
}

}

int main() {
    try {
        const auto device = OpenVulkanTestDevice();
        if (!device) return VulkanTestSkipped;
        const std::uint32_t count = Env("BENCH_K", 100u);
        const std::uint32_t reps = Env("BENCH_REPS", 4u);
        const std::uint32_t groups = Env("BENCH_GROUPS", 4096u);
        const std::uint32_t salt = Env("BENCH_SALT", static_cast<std::uint32_t>(std::chrono::steady_clock::now().time_since_epoch().count()));
        std::mt19937 random(7u);
        std::uniform_real_distribution<float> numerator(1.5f, 3.0f);
        std::uniform_real_distribution<float> denominator(1.0f, 2.0f);
        for (std::uint32_t lane = 0; lane < Threads; ++lane) {
            Input[lane * Stride] = std::bit_cast<std::uint32_t>(numerator(random));
            Input[lane * Stride + 1] = std::bit_cast<std::uint32_t>(denominator(random));
        }
        std::vector<std::uint32_t> userData(8, 0u);
        const auto input = BufferDescriptor(Input.data(), static_cast<std::uint32_t>(Input.size()));
        const auto output = BufferDescriptor(Output.data(), static_cast<std::uint32_t>(Output.size()));
        std::copy(input.begin(), input.end(), userData.begin());
        std::copy(output.begin(), output.end(), userData.begin() + 4);
        std::vector<std::vector<std::uint32_t>> programs;
        for (std::uint32_t rep = 0; rep < reps; ++rep) programs.push_back(Assemble(count, salt + rep));
        const ShaderRecompiler::ShaderComputeStageInfo compute{{Threads, 1, 1}, 0u, {false, false, false}, false, 1};
        std::vector<double> recompile;
        std::vector<double> pipeline;
        std::size_t words = 0;
        std::optional<ShaderRecompiler::RecompileResult> last;
        for (const auto& program : programs) {
            const std::span<const std::uint32_t> span(program);
            const std::array<ShaderRecompiler::MemoryRegion, 1> memory{{{reinterpret_cast<std::uintptr_t>(span.data()), std::as_bytes(span)}}};
            ShaderRecompiler::RecompileRequest request{
                {ShaderStage::Compute, reinterpret_cast<std::uintptr_t>(span.data()), span, 0, {}},
                {32, 0, userData, compute, std::nullopt, std::nullopt, memory},
                device->Target(),
                {0, 0, 0, 128}
            };
            request.useCache = false;
            request.context.floatMode = ShaderRecompiler::ShaderFloatMode{0xf0u, true, true, false};
            auto started = std::chrono::steady_clock::now();
            auto result = ShaderRecompiler::Recompile(request);
            recompile.push_back(Milliseconds(started));
            words = result.spirv.Words().size();
            if (const char* dump = std::getenv("BENCH_DUMP")) {
                if (FILE* file = std::fopen(dump, "wb")) {
                    std::fwrite(result.spirv.Words().data(), 4, words, file);
                    std::fclose(file);
                }
            }
            started = std::chrono::steady_clock::now();
            device->PrebuildComputePipeline(result, reinterpret_cast<std::uintptr_t>(span.data()));
            pipeline.push_back(Milliseconds(started));
            last.emplace(std::move(result));
        }
        Output.fill(0xdeadbeefu);
        device->Dispatch(*last, 1, 1, 1, {}, reinterpret_cast<std::uintptr_t>(programs.back().data()));
        device->WaitIdle();
        std::uint32_t failures = 0;
        for (std::uint32_t lane = 0; lane < Threads; ++lane) {
            const float d = std::bit_cast<float>(Input[lane * Stride]);
            float x = std::bit_cast<float>(Input[lane * Stride + 1]);
            for (std::uint32_t index = 0; index < count; ++index) x = d / x;
            if (Output[lane * Stride] != std::bit_cast<std::uint32_t>(x) || Output[lane * Stride + 1] != salt + reps - 1u) ++failures;
        }
        std::vector<double> dispatches;
        const std::uint32_t runs = Env("BENCH_RUNS", 21u);
        for (std::uint32_t run = 0; run < runs + 3u; ++run) {
            const auto started = std::chrono::steady_clock::now();
            device->Dispatch(*last, groups, 1, 1, {}, reinterpret_cast<std::uintptr_t>(programs.back().data()));
            device->WaitIdle();
            if (run >= 3u) dispatches.push_back(Milliseconds(started));
        }
        std::sort(dispatches.begin(), dispatches.end());
        std::sort(recompile.begin(), recompile.end());
        std::sort(pipeline.begin(), pipeline.end());
        std::printf("k %u words %zu mismatches %u\n", count, words, failures);
        std::printf("recompile_ms");
        for (const double value : recompile) std::printf(" %.1f", value);
        std::printf("\npipeline_ms");
        for (const double value : pipeline) std::printf(" %.1f", value);
        std::printf("\ndispatch_ms min %.2f median %.2f groups %u\n", dispatches.front(), dispatches[dispatches.size() / 2u], groups);
        return failures == 0u ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
