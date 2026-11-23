# Native macOS Linux build

## Repository boundary

`linux-os1` is the main operating-system project.

The Linux kernel is a dedicated git submodule:

```text
kernel/ -> https://github.com/olmox001/Linux-Kernel.git
```

Kernel source changes, kernel portability patches and the case-fold map belong to the kernel repository. Host orchestration and the final OS integration belong to the parent repository.

## Source baseline

- Upstream source archive: Linux 7.3.5
- Local cache path: `kernel/.cache/linux-7.3.5.tar.xz`
- SHA-256: `8f293f83d54f0b7b9be56863ebc5e4ea79d73e530b4ae55c572be97ef39f0990`
- The archive is deliberately not extracted onto the default macOS filesystem before its path audit.

The original archive was previously tracked by the parent repository. It is now retained only as a local, ignored bootstrap cache while the source is migrated into the dedicated kernel repository.

## Verified host baseline

- Host architecture: x86_64
- macOS: 26.5.2
- Darwin: 25.5.0
- Apple Clang: 21.0.0
- Default `ld`: Apple ld 1267
- Default GNU Make: 3.81
- `lld`: available at `/usr/local/bin/lld`
- GNU `objcopy`: available under `/usr/local/opt/binutils/bin/objcopy`
- `pahole`: currently missing

These host defaults are not accepted as the Linux crosschain merely because an executable with the expected name exists.

## Incremental migration

### Step 0 — repository split

Create the dedicated kernel repository and make `kernel/` a git submodule. Preserve the source archive as an ignored local cache. Verify the parent gitlink and the submodule remote.

### Step 1 — filesystem safety

Audit the source archive without extracting it. Catalog every NFD+case-fold collision, every direct reference, and every build-output collision. Define physical host-safe names plus a Linux logical-name map. The initial archive has 13 collision groups / 26 paths.

### Step 2 — kernel source import

Import Linux 7.3.5 into the kernel repository without ever creating the colliding upstream paths in a case-insensitive working tree. Apply the path map during import, update references, and require the tracked-tree case-fold audit to report zero collisions.

### Step 3 — pinned crosschain

Read the kernel's own minimum-tool declarations and build documentation from the imported source before selecting versions. Provide explicit Linux-target tools for compiler, linker, binutils/LLVM utilities and every generated-code dependency. Host utilities must be built for macOS where the kernel build executes them locally.

The build environment must reject an Apple-tool fallback instead of silently accepting it.

### Step 4 — deterministic build contract

Pin source commit, config, compiler suite, linker, binutils/LLVM tools, host tools, locale, timezone, timestamps and Kbuild identity fields. Normalize build paths and all other known nondeterministic inputs.

### Step 5 — equivalence verification

Build the same source/config with the pinned toolchain on a Linux reference host and with the macOS crosschain. Compare the requested final artifacts by SHA-256. Any mismatch must be reduced to a concrete cause before the result is called identical.

The target is not merely “a kernel that boots”; it is a reproducible Linux-target build whose binary-equivalence claim is demonstrated.

## Repository publication note

The parent remote was empty during Step 0. Its first local bootstrap commit still referenced the 155 MB source archive, so GitHub correctly rejected the first push because the blob exceeded the 100 MB file limit.

Because no parent commit had been published, the unpublished bootstrap history was replaced before the first successful push. The public parent history therefore starts from the submodule-based layout and never contains the kernel source archive.
