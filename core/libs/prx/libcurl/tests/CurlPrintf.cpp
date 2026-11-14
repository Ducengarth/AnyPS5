#include "prx/libcurl/src/CurlHost.hpp"
#include "prx/libcurl/libcurl.hpp"
#include "prx/libc/include/ApplicationHeap.hpp"
#include "prx/libc/include/Api.hpp"

#include <array>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>

namespace {

void Require(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "curl printf check failed: %s\n", message);
    std::abort();
}

template <class... Arguments>
void Same(const char* format, Arguments... arguments) {
    std::array<char, 512> guest{};
    std::array<char, 512> host{};
    guest.fill('G');
    host.fill('G');
    const int guestResult = curl_msnprintf_nid_postfix(guest.data(), guest.size(), format, arguments...);
    const int hostResult = curl_msnprintf(host.data(), host.size(), format, arguments...);
    if (guestResult != hostResult || guest != host) {
        std::fprintf(stderr, "format \"%s\": guest %d \"%s\", curl %d \"%s\"\n", format, guestResult, guest.data(), hostResult, host.data());
        std::abort();
    }
}

std::string Guest(const char* format, std::int64_t value) {
    std::array<char, 128> buffer{};
    const int result = curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), format, value);
    Require(result == static_cast<int>(std::strlen(buffer.data())), "the result counts the stored characters");
    return buffer.data();
}

void CheckMatchesCurl() {
    Same("plain text");
    Same("%%");
    Same("100%% %d", 5);
    Same("%d", 42);
    Same("%d", -42);
    Same("%i", INT_MIN);
    Same("%u", 3000000000u);
    Same("%x %X", 0xbeefu, 0xbeefu);
    Same("%o %#o %#o", 8u, 8u, 0u);
    Same("%#x %#X %#x", 255u, 255u, 0u);
    Same("%c%5c|%-5c|", 'A', 'B', 'C');
    Same("%5d|%-5d|%05d|%-05d|", 42, 42, -42, 42);
    Same("%+d % d %+d", 42, 42, -42);
    Same("%.3d %.0d %8.3d|", 7, 0, -7);
    Same("%*d|%*d|", 6, 42, -6, 42);
    Same("%.*d %.*d", 4, 42, -1, 42);
    Same("%-*.*d|", 8, 4, 42);
    Same("%hd %hu", 70000, 70000);
    Same("%s|%10s|%-10s|%.2s|", "text", "text", "text", "text");
    Same("%S %#s %#10s|", "text", "text", "text");
    Same("%s|%.3s|%.5s|%10s|%#s", static_cast<const char*>(nullptr), static_cast<const char*>(nullptr), static_cast<const char*>(nullptr), static_cast<const char*>(nullptr), static_cast<const char*>(nullptr));
    Same("%p %10p| %-10p|", reinterpret_cast<void*>(0x1234), static_cast<void*>(nullptr), static_cast<void*>(nullptr));
    Same("%f %.2f %e %E", 3.25, 3.14159, 12345.678, 0.000123);
    Same("%g %G %#g %+f", 0.0001, 1e20, 1.0, 1.0);
    Same("%10.3f|%-10.3f|%*.*f|", -2.5, 2.5, 12, 3, 3.14159);
    Same("%*f|%.*f", -9, 1.5, -2, 1.5);
    Same("%.400f", 1.5);
    Same("%400.300f|", 123.25);
    Same("%2$s %1$s", "first", "second");
    Same("%1$s %1$s %2$d", "same", 3);
    Same("%2$*1$d|", 6, 42);
    Same("%d %s %f %c", 1, "x", 2.5, 'y');
    Same("%k|", static_cast<void*>(nullptr));
    Same("abc%", static_cast<void*>(nullptr));
    std::string tooMany;
    for (int index = 0; index < 65; ++index) tooMany += "%*d";
    Same(tooMany.c_str());
}

void CheckGuestSizes() {
    Require(Guest("%ld", 0x123456789LL) == "4886718345", "%ld reads a 64-bit long");
    Require(Guest("%ld", INT64_MIN) == "-9223372036854775808", "%ld prints the 64-bit minimum");
    Require(Guest("%lu", -1) == "18446744073709551615", "%lu reads a 64-bit unsigned long");
    Require(Guest("%lx", 0x1122334455667788LL) == "1122334455667788", "%lx reads 64 bits");
    Require(Guest("%zu", 0x10000000000LL) == "1099511627776", "%zu reads a 64-bit size_t");
    Require(Guest("%zd", -1) == "-1", "%zd is a signed long");
    Require(Guest("%Od", 0x7000000000000000LL) == "8070450532247928832", "%Od reads a curl_off_t");
    Require(Guest("%lld", INT64_MAX) == "9223372036854775807", "%lld reads a long long");
    Require(Guest("%qd", -5) == "-5", "%qd reads a long long");
    Require(Guest("%I64d", 7) == "64d", "I is a conversion of its own on the console, not a size");

    std::array<unsigned char, 16> counts{};
    std::array<char, 32> buffer{};
    counts.fill(0xee);
    Require(curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), "abc%n", counts.data()) == 3, "%n output");
    Require(counts[0] == 3 && counts[1] == 0 && counts[3] == 0 && counts[4] == 0xee, "%n stores four bytes");
    counts.fill(0xee);
    curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), "abcd%ln", counts.data());
    Require(counts[0] == 4 && counts[7] == 0 && counts[8] == 0xee, "%ln stores a 64-bit long");
    counts.fill(0xee);
    curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), "ab%hn", counts.data());
    Require(counts[0] == 2 && counts[1] == 0 && counts[2] == 0xee, "%hn stores two bytes");
    counts.fill(0xee);
    curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), "a%lln", counts.data());
    Require(counts[0] == 1 && counts[7] == 0 && counts[8] == 0xee, "%lln stores eight bytes");
}

void CheckBuffers() {
    std::array<char, 16> buffer{};
    buffer.fill('x');
    Require(curl_msnprintf_nid_postfix(buffer.data(), 5, "abcdefgh") == 4, "a full buffer drops the last stored character");
    Require(std::strcmp(buffer.data(), "abcd") == 0 && buffer[5] == 'x', "a full buffer keeps four characters and a terminator");
    buffer.fill('x');
    Require(curl_msnprintf_nid_postfix(buffer.data(), 9, "abcdefgh") == 8 && std::strcmp(buffer.data(), "abcdefgh") == 0, "an exact fit keeps every character");
    buffer.fill('x');
    Require(curl_msnprintf_nid_postfix(buffer.data(), 0, "abc") == 0 && buffer[0] == 'x', "a zero size writes nothing");
    Require(curl_msnprintf_nid_postfix(buffer.data(), 1, "abc") == 0 && buffer[0] == '\0', "a size of one stores only the terminator");
    std::string tooMany;
    for (int index = 0; index < 65; ++index) tooMany += "%*d";
    buffer.fill('x');
    Require(curl_msnprintf_nid_postfix(buffer.data(), buffer.size(), tooMany.c_str()) == -1 && buffer[0] == 'x', "a parameter past 128 fails without output");

    buffer.fill('x');
    Require(curl_msprintf_nid_postfix(buffer.data(), "%s-%d", "a", 5) == 3 && std::strcmp(buffer.data(), "a-5") == 0, "curl_msprintf");
    buffer.fill('x');
    Require(curl_msprintf_nid_postfix(buffer.data(), tooMany.c_str()) == -1 && buffer[0] == '\0', "curl_msprintf terminates even when parsing fails");

    char* text = curl_maprintf_nid_postfix("%s=%d", "key", 7);
    Require(text && std::strcmp(text, "key=7") == 0, "curl_maprintf");
    curl_free(text);
    text = curl_maprintf_nid_postfix("");
    Require(text && text[0] == '\0', "an empty curl_maprintf result is an empty string");
    curl_free(text);
    Require(curl_maprintf_nid_postfix(tooMany.c_str()) == nullptr, "curl_maprintf returns null when parsing fails");
    text = curl_maprintf_nid_postfix("%7999999s", "");
    Require(text && std::strlen(text) == 7999999, "curl_maprintf keeps 7999999 characters");
    curl_free(text);
    Require(curl_maprintf_nid_postfix("%8000000s", "") == nullptr, "curl_maprintf stops at the 8000000-byte dynamic buffer limit");
}

void CheckStreams() {
    const auto path = (std::filesystem::temp_directory_path() / "anyps5-curl-printf.txt").string();
    FileStream* stream = fopen_nid_postfix(path.c_str(), "w+");
    Require(stream != nullptr, "open a guest stream");
    Require(curl_mfprintf_nid_postfix(reinterpret_cast<std::FILE*>(stream), "%d-%s|", 5, "x") == 4, "curl_mfprintf counts its characters");
    rewind_nid_postfix(stream);
    std::array<char, 16> read{};
    Require(fread_nid_postfix(read.data(), 1, read.size(), stream) == 4 && std::memcmp(read.data(), "5-x|", 4) == 0, "curl_mfprintf writes through the guest stream");
    fclose_nid_postfix(stream);
    std::filesystem::remove(path);
}

}

int main() {
    ApplicationHeapInitialize_nid_no_patch(ApplicationEmptyProcessParameters_nid_no_patch());
    CheckMatchesCurl();
    CheckGuestSizes();
    CheckBuffers();
    CheckStreams();
    std::puts("curl printf tests passed");
    return 0;
}
