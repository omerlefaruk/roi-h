#define UNICODE
#define _UNICODE
#include <errno.h>
#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <windows.h>

int wmain(int argc, wchar_t **argv) {
    wchar_t package_root[32768];
    DWORD length = GetModuleFileNameW(NULL, package_root, 32768);
    if (length == 0 || length == 32768) {
        fwprintf(stderr, L"ROI-H launcher could not locate its package.\n");
        return 1;
    }
    wchar_t *separator = wcsrchr(package_root, L'\\');
    if (separator == NULL) {
        fwprintf(stderr, L"ROI-H launcher received an invalid package path.\n");
        return 1;
    }
    *separator = L'\0';

    wchar_t python[32768];
    if (swprintf_s(python, 32768, L"%ls\\runtime\\python.exe", package_root) < 0) {
        return 1;
    }
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        fwprintf(stderr, L"ROI-H packaged Python is missing.\n");
        return 1;
    }

    wchar_t local_app_data[32768];
    DWORD local_length = GetEnvironmentVariableW(L"LOCALAPPDATA", local_app_data, 32768);
    if (local_length == 0 || local_length >= 32768) {
        fwprintf(stderr, L"ROI-H launcher requires LOCALAPPDATA.\n");
        return 1;
    }
    wchar_t browser_root[32768];
    if (swprintf_s(
            browser_root,
            32768,
            L"%ls\\ROI-H\\Browsers",
            local_app_data) < 0) {
        return 1;
    }
    SetEnvironmentVariableW(L"ROI_H_INSTALL_ROOT", package_root);
    SetEnvironmentVariableW(L"PLAYWRIGHT_BROWSERS_PATH", browser_root);
    SetEnvironmentVariableW(L"PLAYWRIGHT_SKIP_BROWSER_GC", L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");

    wchar_t **child_argv = calloc((size_t)argc + 4, sizeof(wchar_t *));
    if (child_argv == NULL) {
        return 1;
    }
    child_argv[0] = python;
    child_argv[1] = L"-m";
    child_argv[2] = L"roi_h";
    for (int index = 1; index < argc; ++index) {
        child_argv[index + 2] = argv[index];
    }
    child_argv[argc + 2] = NULL;

    intptr_t result = _wspawnv(_P_WAIT, python, (const wchar_t *const *)child_argv);
    free(child_argv);
    if (result == -1) {
        fwprintf(stderr, L"ROI-H launcher failed to start Python (errno=%d).\n", errno);
        return 1;
    }
    return (int)result;
}
