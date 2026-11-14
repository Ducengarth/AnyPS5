#include "prx/libcurl/src/Mprintf.hpp"
#include "prx/libc/include/Api.hpp"
#include "prx/libc/include/General.hpp"
#include "prx/libc/include/GuestArena.hpp"
#include "prx/libc/include/WindowsFormatting.hpp"

#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>

namespace {

constexpr int MaxParameters = 128;
constexpr std::size_t WorkSize = 326;
constexpr char LowerDigits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
constexpr char UpperDigits[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

enum class Type { Unknown, String, Pointer, Int, IntPointer, Double, Width };

enum : int {
    FlagSpace = 1 << 0,
    FlagShowSign = 1 << 1,
    FlagLeft = 1 << 2,
    FlagAlt = 1 << 3,
    FlagShort = 1 << 4,
    FlagLong = 1 << 5,
    FlagLongLong = 1 << 6,
    FlagLongDouble = 1 << 7,
    FlagPadNil = 1 << 8,
    FlagUnsigned = 1 << 9,
    FlagOctal = 1 << 10,
    FlagHex = 1 << 11,
    FlagUpper = 1 << 12,
    FlagWidth = 1 << 13,
    FlagWidthParam = 1 << 14,
    FlagPrecision = 1 << 15,
    FlagPrecisionParam = 1 << 16,
    FlagChar = 1 << 17,
    FlagFloatE = 1 << 18,
    FlagFloatG = 1 << 19,
};

struct Parameter {
    Type type = Type::Unknown;
    int flags = 0;
    std::int64_t width = 0;
    std::int64_t precision = 0;
    std::uint64_t bits = 0;
    bool described = false;

    std::int64_t Signed() const { return static_cast<std::int64_t>(bits); }
    double Number() const { return std::bit_cast<double>(bits); }
};

using Parameters = std::array<Parameter, MaxParameters>;
using Ends = std::array<const char*, MaxParameters>;

bool IsDigit(char character) {
    return character >= '0' && character <= '9';
}

std::int64_t DollarString(const char* input, const char** end) {
    int number = 0;
    while (IsDigit(*input)) {
        if (number < MaxParameters) {
            number *= 10;
            number += *input - '0';
        }
        ++input;
    }
    if (number <= MaxParameters && *input == '$') {
        *end = ++input;
        return number;
    }
    return 0;
}

bool IsQualifier(char character) {
    switch (character) {
    case '-': case '+': case ' ': case '#': case '.':
    case '0': case '1': case '2': case '3': case '4':
    case '5': case '6': case '7': case '8': case '9':
    case 'h': case 'l': case 'L': case 'z': case 'q':
    case '*': case 'O':
        return true;
    default:
        return false;
    }
}

std::int64_t ParseLong(const char* text, const char** end) {
    char* stop = nullptr;
    const auto value = std::strtoll(text, &stop, 10);
    *end = stop;
    return value;
}

bool Describe(const char* format, Parameters& parameters, Ends& ends, std::int64_t& maxParameter) {
    const char* fmt = format;
    std::int64_t parameterNumber = 0;
    std::size_t end = 0;
    maxParameter = 0;
    while (*fmt) {
        if (*fmt++ != '%') continue;
        if (*fmt == '%') {
            ++fmt;
            continue;
        }
        int flags = 0;
        ++parameterNumber;
        auto thisParameter = DollarString(fmt, &fmt);
        if (thisParameter == 0) thisParameter = parameterNumber;
        if (thisParameter > maxParameter) maxParameter = thisParameter;
        std::int64_t width = 0;
        std::int64_t precision = 0;
        while (IsQualifier(*fmt)) {
            switch (*fmt++) {
            case ' ':
                flags |= FlagSpace;
                break;
            case '+':
                flags |= FlagShowSign;
                break;
            case '-':
                flags |= FlagLeft;
                flags &= ~FlagPadNil;
                break;
            case '#':
                flags |= FlagAlt;
                break;
            case '.':
                if (*fmt == '*') {
                    flags |= FlagPrecisionParam;
                    ++fmt;
                    ++parameterNumber;
                    const auto index = DollarString(fmt, &fmt);
                    precision = index ? index : parameterNumber;
                    if (precision > maxParameter) maxParameter = precision;
                } else {
                    flags |= FlagPrecision;
                    precision = ParseLong(fmt, &fmt);
                }
                break;
            case 'h':
                flags |= FlagShort;
                break;
            case 'l':
                flags |= (flags & FlagLong) ? FlagLongLong : FlagLong;
                break;
            case 'L':
                flags |= FlagLongDouble;
                break;
            case 'q':
                flags |= FlagLongLong;
                break;
            case 'z':
            case 'O':
                flags |= FlagLong;
                break;
            case '0':
                if (!(flags & FlagLeft)) flags |= FlagPadNil;
                [[fallthrough]];
            case '1': case '2': case '3': case '4':
            case '5': case '6': case '7': case '8': case '9':
                flags |= FlagWidth;
                width = ParseLong(fmt - 1, &fmt);
                break;
            case '*': {
                flags |= FlagWidthParam;
                ++parameterNumber;
                const auto index = DollarString(fmt, &fmt);
                width = index ? index : parameterNumber;
                if (width > maxParameter) maxParameter = width;
                break;
            }
            default:
                break;
            }
        }
        const auto index = thisParameter - 1;
        if (index < 0 || index >= MaxParameters) return false;
        auto& parameter = parameters[static_cast<std::size_t>(index)];
        switch (*fmt) {
        case 'S':
            flags |= FlagAlt;
            [[fallthrough]];
        case 's':
            parameter.type = Type::String;
            break;
        case 'n':
            parameter.type = Type::IntPointer;
            break;
        case 'p':
            parameter.type = Type::Pointer;
            break;
        case 'd': case 'i':
            parameter.type = Type::Int;
            break;
        case 'u':
            parameter.type = Type::Int;
            flags |= FlagUnsigned;
            break;
        case 'o':
            parameter.type = Type::Int;
            flags |= FlagOctal;
            break;
        case 'x':
            parameter.type = Type::Int;
            flags |= FlagHex | FlagUnsigned;
            break;
        case 'X':
            parameter.type = Type::Int;
            flags |= FlagHex | FlagUpper | FlagUnsigned;
            break;
        case 'c':
            parameter.type = Type::Int;
            flags |= FlagChar;
            break;
        case 'f':
            parameter.type = Type::Double;
            break;
        case 'e':
            parameter.type = Type::Double;
            flags |= FlagFloatE;
            break;
        case 'E':
            parameter.type = Type::Double;
            flags |= FlagFloatE | FlagUpper;
            break;
        case 'g':
            parameter.type = Type::Double;
            flags |= FlagFloatG;
            break;
        case 'G':
            parameter.type = Type::Double;
            flags |= FlagFloatG | FlagUpper;
            break;
        default:
            parameter.type = Type::Unknown;
            break;
        }
        parameter.flags = flags;
        parameter.width = width;
        parameter.precision = precision;
        parameter.described = true;
        if (flags & FlagWidthParam) {
            const auto slot = width - 1;
            if (slot < 0 || slot >= MaxParameters) return false;
            parameter.width = slot;
            auto& widthParameter = parameters[static_cast<std::size_t>(slot)];
            widthParameter.type = Type::Width;
            widthParameter.flags = 0;
            widthParameter.width = 0;
            widthParameter.precision = 0;
            widthParameter.described = true;
        }
        if (flags & FlagPrecisionParam) {
            const auto slot = precision - 1;
            if (slot < 0 || slot >= MaxParameters) return false;
            parameter.precision = slot;
            auto& precisionParameter = parameters[static_cast<std::size_t>(slot)];
            precisionParameter.type = Type::Width;
            precisionParameter.flags = 0;
            precisionParameter.width = 0;
            precisionParameter.precision = 0;
            precisionParameter.described = true;
        }
        if (end >= ends.size()) NotImplemented_nid_no_patch("curl_mprintf: more than 128 conversions");
        ends[end++] = fmt + (*fmt == '\0' ? 0 : 1);
    }
    return true;
}

void Read(Parameters& parameters, std::int64_t maxParameter, const VaList* arguments) {
    LibcDetail::FormatArguments list(arguments);
    for (std::int64_t index = 0; index < maxParameter; ++index) {
        auto& parameter = parameters[static_cast<std::size_t>(index)];
        if (!parameter.described) NotImplemented_nid_no_patch("curl_mprintf: a positional parameter below the highest one is not used by any conversion");
        if (parameter.flags & FlagWidthParam) parameters[static_cast<std::size_t>(parameter.width)].bits = static_cast<std::uint64_t>(static_cast<std::int64_t>(list.Next<int>()));
        if (parameter.flags & FlagPrecisionParam) parameters[static_cast<std::size_t>(parameter.precision)].bits = static_cast<std::uint64_t>(static_cast<std::int64_t>(list.Next<int>()));
        switch (parameter.type) {
        case Type::String:
        case Type::IntPointer:
        case Type::Unknown:
        case Type::Pointer:
            parameter.bits = list.Next<std::uint64_t>();
            break;
        case Type::Int:
            if ((parameter.flags & FlagLongLong) || (parameter.flags & FlagLong)) parameter.bits = list.Next<std::uint64_t>();
            else if (parameter.flags & FlagUnsigned) parameter.bits = list.Next<unsigned int>();
            else parameter.bits = static_cast<std::uint64_t>(static_cast<std::int64_t>(list.Next<int>()));
            break;
        case Type::Double:
            parameter.bits = std::bit_cast<std::uint64_t>(list.Next<double>());
            break;
        case Type::Width:
            parameter.type = Type::Int;
            break;
        }
    }
}

void StoreCount(void* destination, const void* value, std::size_t bytes) {
    if (!destination) NotImplemented_nid_no_patch("curl_mprintf: %n with a null pointer");
    const GuestArena::HostWrite write(destination, bytes);
    std::memcpy(destination, value, bytes);
}

}

namespace CurlPrintf {

int Format(void* data, Stream stream, const char* format, const VaList* arguments) {
    Parameters parameters{};
    Ends ends{};
    std::int64_t maxParameter = 0;
    if (!Describe(format, parameters, ends, maxParameter)) return -1;
    Read(parameters, maxParameter, arguments);
    const char* digits = LowerDigits;
    int done = 0;
    const auto put = [&](char character) {
        if (stream(static_cast<unsigned char>(character), data) == -1) return false;
        ++done;
        return true;
    };
    std::int64_t parameterNumber = 0;
    std::size_t end = 0;
    const char* f = format;
    char work[WorkSize];
    char* const workEnd = &work[sizeof(work) - 2];
    while (*f != '\0') {
        if (*f != '%') {
            do {
                if (!put(*f)) return done;
            } while (*++f && *f != '%');
            continue;
        }
        ++f;
        if (*f == '%') {
            ++f;
            if (!put('%')) return done;
            continue;
        }
        auto parameterIndex = DollarString(f, &f);
        if (!parameterIndex) parameterIndex = parameterNumber;
        else --parameterIndex;
        ++parameterNumber;
        auto& p = parameters[static_cast<std::size_t>(parameterIndex)];
        std::int64_t width = 0;
        if (p.flags & FlagWidthParam) {
            width = parameters[static_cast<std::size_t>(p.width)].Signed();
            ++parameterNumber;
            if (width < 0) {
                width = -width;
                p.flags |= FlagLeft;
                p.flags &= ~FlagPadNil;
            }
        } else {
            width = p.width;
        }
        std::int64_t precision = -1;
        if (p.flags & FlagPrecisionParam) {
            precision = parameters[static_cast<std::size_t>(p.precision)].Signed();
            ++parameterNumber;
            if (precision < 0) precision = -1;
        } else if (p.flags & FlagPrecision) {
            precision = p.precision;
        }
        int alternate = (p.flags & FlagAlt) ? 1 : 0;
        std::uint64_t base = 10;
        std::uint64_t number = 0;
        bool negative = false;
        bool formatNumber = false;
        switch (p.type) {
        case Type::Int:
            number = p.bits;
            if (p.flags & FlagChar) {
                if (!(p.flags & FlagLeft))
                    while (--width > 0)
                        if (!put(' ')) return done;
                if (!put(static_cast<char>(number))) return done;
                if (p.flags & FlagLeft)
                    while (--width > 0)
                        if (!put(' ')) return done;
                break;
            }
            if (p.flags & FlagOctal) {
                base = 8;
            } else if (p.flags & FlagHex) {
                digits = (p.flags & FlagUpper) ? UpperDigits : LowerDigits;
                base = 16;
            } else if (!(p.flags & FlagUnsigned)) {
                negative = p.Signed() < 0;
                if (negative) number = static_cast<std::uint64_t>(-(p.Signed() + 1)) + 1;
            }
            formatNumber = true;
            break;
        case Type::String: {
            static constexpr char Null[] = "(nil)";
            const char* text = reinterpret_cast<const char*>(p.bits);
            std::size_t length = 0;
            if (!text) {
                if (precision == -1 || precision >= static_cast<std::int64_t>(sizeof(Null)) - 1) {
                    text = Null;
                    length = sizeof(Null) - 1;
                    p.flags &= ~FlagAlt;
                } else {
                    text = "";
                    length = 0;
                }
            } else if (precision != -1) {
                length = static_cast<std::size_t>(precision);
            } else {
                length = std::strlen(text);
            }
            width -= length > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max()) ? std::numeric_limits<std::int64_t>::max() : static_cast<std::int64_t>(length);
            if (p.flags & FlagAlt)
                if (!put('"')) return done;
            if (!(p.flags & FlagLeft))
                while (width-- > 0)
                    if (!put(' ')) return done;
            for (; length && *text; --length)
                if (!put(*text++)) return done;
            if (p.flags & FlagLeft)
                while (width-- > 0)
                    if (!put(' ')) return done;
            if (p.flags & FlagAlt)
                if (!put('"')) return done;
            break;
        }
        case Type::Pointer:
            if (p.bits) {
                base = 16;
                digits = (p.flags & FlagUpper) ? UpperDigits : LowerDigits;
                alternate = 1;
                number = p.bits;
                formatNumber = true;
            } else {
                static constexpr char Nil[] = "(nil)";
                width -= static_cast<std::int64_t>(sizeof(Nil) - 1);
                if (p.flags & FlagLeft)
                    while (width-- > 0)
                        if (!put(' ')) return done;
                for (const char* point = Nil; *point; ++point)
                    if (!put(*point)) return done;
                if (!(p.flags & FlagLeft))
                    while (width-- > 0)
                        if (!put(' ')) return done;
            }
            break;
        case Type::Double: {
            std::string specification = "%";
            width = -1;
            if (p.flags & FlagWidth) width = p.width;
            else if (p.flags & FlagWidthParam) width = parameters[static_cast<std::size_t>(p.width)].Signed();
            precision = -1;
            if (p.flags & FlagPrecision) precision = p.precision;
            else if (p.flags & FlagPrecisionParam) precision = parameters[static_cast<std::size_t>(p.precision)].Signed();
            if (p.flags & FlagLeft) specification += '-';
            if (p.flags & FlagShowSign) specification += '+';
            if (p.flags & FlagSpace) specification += ' ';
            if (p.flags & FlagAlt) specification += '#';
            if (width >= 0) {
                if (width >= static_cast<std::int64_t>(sizeof(work))) width = static_cast<std::int64_t>(sizeof(work)) - 1;
                specification += std::to_string(width);
            }
            if (precision >= 0) {
                std::uint64_t maxPrecision = sizeof(work) - 2;
                double value = p.Number();
                if (value == std::numeric_limits<double>::infinity()) NotImplemented_nid_no_patch("curl_mprintf: a precision with positive infinity");
                if (width > 0 && precision <= width) maxPrecision -= static_cast<std::uint64_t>(width);
                while (value >= 10.0) {
                    value /= 10;
                    --maxPrecision;
                }
                if (precision > static_cast<std::int64_t>(maxPrecision)) precision = static_cast<std::int64_t>(maxPrecision) - 1;
                if (precision < 0) precision = 0;
                specification += '.';
                specification += std::to_string(precision);
            }
            if (p.flags & FlagLong) specification += 'l';
            if (p.flags & FlagFloatE) specification += (p.flags & FlagUpper) ? 'E' : 'e';
            else if (p.flags & FlagFloatG) specification += (p.flags & FlagUpper) ? 'G' : 'g';
            else specification += 'f';
            snprintf_nid_postfix(work, sizeof(work), specification.c_str(), p.Number());
            for (const char* point = work; *point; ++point)
                if (!put(*point)) return done;
            break;
        }
        case Type::IntPointer:
            if (p.flags & (FlagLongLong | FlagLong)) {
                const auto count = static_cast<std::int64_t>(done);
                StoreCount(reinterpret_cast<void*>(p.bits), &count, sizeof(count));
            } else if (!(p.flags & FlagShort)) {
                const auto count = static_cast<std::int32_t>(done);
                StoreCount(reinterpret_cast<void*>(p.bits), &count, sizeof(count));
            } else {
                const auto count = static_cast<std::int16_t>(done);
                StoreCount(reinterpret_cast<void*>(p.bits), &count, sizeof(count));
            }
            break;
        default:
            break;
        }
        if (formatNumber) {
            if (precision == -1) precision = 1;
            char* w = workEnd;
            while (number > 0) {
                *w-- = digits[number % base];
                number /= base;
            }
            width -= static_cast<std::int64_t>(workEnd - w);
            precision -= static_cast<std::int64_t>(workEnd - w);
            if (alternate && base == 8 && precision <= 0) {
                *w-- = '0';
                --width;
            }
            if (precision > 0) {
                width -= precision;
                while (precision-- > 0 && w >= work) *w-- = '0';
            }
            if (alternate && base == 16) width -= 2;
            if (negative || (p.flags & FlagShowSign) || (p.flags & FlagSpace)) --width;
            if (!(p.flags & FlagLeft) && !(p.flags & FlagPadNil))
                while (width-- > 0)
                    if (!put(' ')) return done;
            if (negative) {
                if (!put('-')) return done;
            } else if (p.flags & FlagShowSign) {
                if (!put('+')) return done;
            } else if (p.flags & FlagSpace) {
                if (!put(' ')) return done;
            }
            if (alternate && base == 16) {
                if (!put('0')) return done;
                if (!put((p.flags & FlagUpper) ? 'X' : 'x')) return done;
            }
            if (!(p.flags & FlagLeft) && (p.flags & FlagPadNil))
                while (width-- > 0)
                    if (!put('0')) return done;
            while (++w <= workEnd)
                if (!put(*w)) return done;
            if (p.flags & FlagLeft)
                while (width-- > 0)
                    if (!put(' ')) return done;
        }
        f = ends[end++];
    }
    return done;
}

}
