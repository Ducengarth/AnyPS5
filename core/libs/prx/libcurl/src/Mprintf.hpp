#ifndef CORE_LIBS_PRX_LIBCURL_SRC_MPRINTF_HPP
#define CORE_LIBS_PRX_LIBCURL_SRC_MPRINTF_HPP

#include "SceTypes.hpp"

namespace CurlPrintf {

using Stream = int (*)(int character, void* data);

int Format(void* data, Stream stream, const char* format, const VaList* arguments);

}

#endif
