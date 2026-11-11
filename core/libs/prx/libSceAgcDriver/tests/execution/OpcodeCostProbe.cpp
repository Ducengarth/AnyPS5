#include "prx/libSceAgcDriver/Execution/include/VulkanDevice.hpp"
#include "Recompiler.hpp"
#include "VulkanTestDevice.hpp"
#include <spirv/unified1/spirv.hpp>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <span>
#include <string>
#include <vector>

namespace {

using ShaderRecompiler::ShaderStage;

constexpr std::uint32_t Threads = 64;
alignas(256) std::array<std::uint32_t, Threads * 4> Input{};
alignas(256) std::array<std::uint32_t, Threads * 4> Output{};

std::array<std::uint32_t, 4> BufferDescriptor(const void* data, std::uint32_t bytes) {
    const auto address = reinterpret_cast<std::uintptr_t>(data);
    return {static_cast<std::uint32_t>(address), static_cast<std::uint32_t>((address >> 32u) & 0xffffu), bytes, 0x31016facu};
}

std::vector<std::uint32_t> Assemble(bool packed, std::uint32_t op, std::uint32_t count, bool literal = false) {
    std::vector<std::uint32_t> code{0x34020084u, 0x34060088u, 0xe0381000u, 0x80000401u, 0xbf8c3f70u};
    std::uint32_t previous = 4;
    for (std::uint32_t i = 0; i < count; ++i) {
        const std::uint32_t dst = 16 + (i % 200);
        const std::uint32_t src0 = 256 + previous;
        const std::uint32_t src1 = 256 + 5;
        const std::uint32_t src2 = literal ? 255u : 256u + 6u;
        if (packed) {
            code.push_back(0xcc000000u | (op << 16u) | (1u << 14u) | dst);
            code.push_back((3u << 27u) | (src2 << 18u) | (src1 << 9u) | src0);
        } else {
            code.push_back(0xd4000000u | (op << 16u) | dst);
            code.push_back((src2 << 18u) | (src1 << 9u) | src0);
        }
        if (literal) code.push_back(0x07060302u);
        previous = dst;
    }
    code.push_back(0xe0701000u);
    code.push_back(0x80010003u | (previous << 8u));
    code.push_back(0xbf810000u);
    return code;
}

long Words(AgcDriver::VulkanDevice& device, const std::vector<std::uint32_t>& code, bool int16) {
    std::vector<std::uint32_t> userData(8, 0u);
    const auto input = BufferDescriptor(Input.data(), static_cast<std::uint32_t>(Input.size() * 4u));
    const auto output = BufferDescriptor(Output.data(), static_cast<std::uint32_t>(Output.size() * 4u));
    std::copy(input.begin(), input.end(), userData.begin());
    std::copy(output.begin(), output.end(), userData.begin() + 4);
    const std::span<const std::uint32_t> span(code);
    const std::array<ShaderRecompiler::MemoryRegion, 1> memory{{{reinterpret_cast<std::uintptr_t>(span.data()), std::as_bytes(span)}}};
    const ShaderRecompiler::ShaderComputeStageInfo compute{{Threads, 1, 1}, 0u, {false, false, false}, false, 1};
    ShaderRecompiler::RecompileRequest request{
        {ShaderStage::Compute, reinterpret_cast<std::uintptr_t>(span.data()), span, 0, {}},
        {32, 0, userData, compute, std::nullopt, std::nullopt, memory},
        device.Target(),
        {0, 0, 0, 128}
    };
    std::vector<std::uint32_t> capabilities(request.target.supportedCapabilities.begin(), request.target.supportedCapabilities.end());
    if (!int16) std::erase(capabilities, static_cast<std::uint32_t>(spv::CapabilityInt16));
    request.target.supportedCapabilities = capabilities;
    request.useCache = false;
    try {
        const auto result = ShaderRecompiler::Recompile(request);
        if (const char* path = std::getenv("PROBE_DUMP_FILE")) {
            std::ofstream out(path, std::ios::binary);
            out.write(reinterpret_cast<const char*>(result.spirv.Words().data()), static_cast<std::streamsize>(result.spirv.Words().size() * 4u));
        }
        return static_cast<long>(result.spirv.Words().size());
    } catch (const std::exception&) {
        return -1;
    }
}

}

int main() {
    try {
        const auto device = OpenVulkanTestDevice();
        if (!device) return VulkanTestSkipped;
        const bool int16 = std::getenv("PROBE_NO_INT16") == nullptr;
        if (const char* dump = std::getenv("PROBE_DUMP_OP")) {
            const auto op = static_cast<std::uint32_t>(std::strtoul(dump, nullptr, 16));
            const auto count = static_cast<std::uint32_t>(std::strtoul(std::getenv("PROBE_DUMP_COUNT"), nullptr, 10));
            std::printf("dump %ld\n", Words(*device, Assemble(std::getenv("PROBE_DUMP_PACKED") != nullptr, op, count), int16));
            return 0;
        }
        constexpr std::uint32_t K = 32;
        const long base = Words(*device, Assemble(false, 0x30fu, 0), int16);
        std::printf("base %ld int16 %d\n", base, int16 ? 1 : 0);
        for (std::uint32_t op = 0x100; op < 0x380; ++op) {
            const long words = Words(*device, Assemble(false, op, K), int16);
            if (words > 0) std::printf("vop3 0x%03x %.1f\n", op, static_cast<double>(words - base) / K);
            const long literalWords = Words(*device, Assemble(false, op, K, true), int16);
            if (literalWords > 0) std::printf("vop3lit 0x%03x %.1f\n", op, static_cast<double>(literalWords - base) / K);
        }
        for (std::uint32_t op = 0; op < 0x40; ++op) {
            const long words = Words(*device, Assemble(true, op, K), int16);
            if (words > 0) std::printf("vop3p 0x%02x %.1f\n", op, static_cast<double>(words - base) / K);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
