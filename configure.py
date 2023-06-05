import argparse
import io
import os
import sys
import shutil
import platform
import requests
import zipfile
from ninja import ninja_syntax
from pathlib import Path
from typing import Optional, List


MUSL_CROSS_MAKE_COMMIT = "fe915821b652a7fa37b34a596f47d8e20bc72338"


class Args:
    no_patches = (bool,)
    prefix = (str,)

    host = (Optional[str],)
    target = (str,)

    cc = (str,)
    cxx = (str,)
    cc_build = (str,)
    cxx_build = (str,)
    cc_flags = (str,)
    cxx_flags = (str,)
    ld_flags = (str,)
    enable_cache = (bool,)

    binutils_flags = (List[str],)
    gcc_flags = (List[str],)
    gcc_with_isl = (bool,)

    binutils_version = (str,)
    gcc_version = (str,)
    gmp_version = (str,)
    isl_version = (str,)
    linux_version = (str,)
    mpc_version = (str,)
    mpfr_version = (str,)
    musl_version = (str,)

    _make = (str,)

    def __init__(self, args: argparse.Namespace) -> None:
        self.no_patches = args.no_patches
        self.prefix = args.prefix

        self.host = args.host
        self.target = args.target

        self.cc = args.cc
        self.cxx = args.cxx
        self.cc_build = args.cc_build
        self.cxx_build = args.cxx_build
        self.cc_flags = args.cc_flags
        self.cxx_flags = args.cxx_flags
        self.ld_flags = args.ld_flags
        self.enable_cache = args.enable_cache

        # configure options
        self.binutils_flags = [
            "--disable-separate-code",
            "--disable-werror",
            "--target=$target",
            "--prefix=",
            "--libdir=/lib",
            "--disable-multilib",
            "--with-sysroot=/$target",
            "--enable-deterministic-archives",
        ]

        if self.host:
            self.binutils_flags.append("--host=$host")

        if args.binutils_flags:
            self.binutils_flags.extend(args.binutils_flags.split(" "))

        self.gcc_flags = [
            "--enable-languages=c,c++",
            "--disable-bootstrap",
            "--disable-werror",
            "--target=$target",
            "--prefix=",
            "--libdir=/lib",
            "--disable-multilib",
            "--with-sysroot=/$target",
            "--enable-tls",
            "--disable-libmudflap",
            "--disable-libsanitizer",
            "--disable-gnu-indirect-function",
            "--disable-libmpx",
            "--enable-initfini-array",
            "--enable-libstdcxx-time=rt",
            "--with-build-sysroot=$build_sysroot_dir",
        ]

        if self.host:
            self.gcc_flags.append("--host=$host")

        if "fdpic" in self.target:
            self.gcc_flags.append("--enable-fdpic")

        if self.target.startswith("x86_64") and self.target.endswith("x32"):
            self.gcc_flags.append("--with-abi=x32")

        if "powerpc64" in self.target:
            self.gcc_flags.append("--with-abi=elfv2")

        if "mips64" in self.target or "mipsisa64" in self.target:
            if "n32" in self.target:
                self.gcc_flags.append("--with-abi=n32")
            else:
                self.gcc_flags.append("--with-abi=64")

        if "s390x" in self.target:
            self.gcc_flags.append("--with-long-double-128")

        if self.target.endswith("sf"):
            self.gcc_flags.append("--with-float=soft")
        elif self.target.endswith("hf"):
            self.gcc_flags.append("--with-float=hard")

        if args.gcc_flags:
            self.gcc_flags.extend(args.gcc_flags.split(" "))

        self.gcc_with_isl = args.gcc_with_isl

        self.binutils_version = args.binutils_version
        self.gcc_version = args.gcc_version
        self.gmp_version = args.gmp_version
        self.isl_version = args.isl_version
        self.linux_version = args.linux_version
        self.mpc_version = args.mpc_version
        self.mpfr_version = args.mpfr_version
        self.musl_version = args.musl_version

        self._make = "make"

    def is_cross(self) -> bool:
        return self.host is None

    def dependencies_summary(self) -> None:
        print("\nDependencies:")
        print(f"  binutils {self.binutils_version}")
        print(f"       gcc {self.gcc_version}")
        print(f"       gmp {self.gmp_version}")

        if self.gcc_with_isl:
            print(f"       isl {self.isl_version}")

        print(f"     linux {self.linux_version}")
        print(f"       mpc {self.mpc_version}")
        print(f"      mpfr {self.mpfr_version}\n")

    @staticmethod
    def _exists(cmd: str, msg: str) -> bool:
        path = shutil.which(cmd)
        if path is not None:
            print(f"{msg}: {cmd} ({path})")
            return True
        else:
            print(f"{msg}: {cmd} (doesn't exists)")
            return False

    def try_get_tools(self):
        failed = False

        if not self._exists(self.cc_build, "Checking for build C compiler"):
            failed = True
        if not self._exists(self.cxx_build, "Checking for build C++ compiler"):
            failed = True

        if self.host:
            self.cc = self.cc.replace("$host", self.host).lstrip("-")
            self.cxx = self.cxx.replace("$host", self.host).lstrip("-")

            if not self._exists(self.cc, "Checking for host C compiler"):
                failed = True
            if not self._exists(self.cxx, "Checking for host C++ compiler"):
                failed = True
        else:
            self.cc = self.cc_build
            self.cxx = self.cxx_build

        if self.enable_cache:
            ccache = self._exists("ccache", "Checking for tool ccache")
            sccache = None
            wrapper = None

            if ccache:
                wrapper = "ccache"
            else:
                sccache = self._exists("sccache", "Checking for tool sccache")

                if sccache:
                    wrapper = "sccache"

            if wrapper:
                print(f"Using {wrapper} as compiler wrapper")
                self.cc = f"{wrapper} {self.cc}"
                self.cxx = f"{wrapper} {self.cxx}"
                self.cc_build = f"{wrapper} {self.cc_build}"
                self.cxx_build = f"{wrapper} {self.cxx_build}"

        if self._exists("make", "Checking for tool make"):
            self._make = "make"
        elif self._exists("gmake", "Checking for tool gmake"):
            self._make = "gmake"
        elif self._exists("mingw32-make", "Checking for tool mingw32-make"):
            self._make = "mingw32-make"
        else:
            failed = True

        if not self._exists("curl", "Checking for tool curl"):
            failed = True

        if not self._exists("patch", "Checking for tool patch"):
            failed = True

        if not self._exists("tar", "Checking for tool tar"):
            failed = True

        return failed

    def ninja(self) -> None:
        cpu_count = os.cpu_count()
        print("Writing build.ninja")
        with open("build.ninja", "w") as f:
            writer = ninja_syntax.Writer(f)
            writer.variable("target", self.target)
            writer.variable("host", self.host)
            writer.newline()
            writer.variable("cc", self.cc)
            writer.variable("cxx", self.cxx)

            if not self.is_cross():
                writer.variable("cc_build", self.cc_build)
                writer.variable("cxx_build", self.cxx_build)

            writer.variable("cc_flags", self.cc_flags)
            writer.variable("cxx_flags", self.cxx_flags)
            writer.variable("ld_flags", self.ld_flags)
            writer.newline()
            writer.variable("binutils_version", self.binutils_version)
            writer.variable("gcc_version", self.gcc_version)
            writer.variable("gmp_version", self.gmp_version)

            if self.gcc_with_isl:
                writer.variable("isl_version", self.isl_version)

            writer.variable("linux_version", self.linux_version)
            writer.variable("mpc_version", self.mpc_version)
            writer.variable("mpfr_version", self.mpfr_version)
            writer.variable("musl_version", self.musl_version)
            writer.newline()
            writer.variable("gnu_site", "https://ftpmirror.gnu.org")

            if self.gcc_with_isl:
                writer.variable("isl_site", "https://libisl.sourceforge.io")

            writer.variable(
                "linux_site", "https://cdn.kernel.org/pub/linux/kernel/v6.x"
            )
            writer.variable("musl_site", "https://www.musl-libc.org")
            writer.newline()
            writer.variable("download_command", f"curl -L -o")
            writer.variable(
                "make_command",
                f"{self._make} -j {cpu_count} MULTILIB_OSDIRNAMES= INFO_DEPS= infodir= ac_cv_prog_lex_root=lex.yy",
            )
            writer.newline()
            writer.comment("edit below this line carefully")
            writer.newline()
            env_vars = 'CC="$cc" CXX="$cxx" CFLAGS="$cc_flags" CXXFLAGS="$cxx_flags" LDFLAGS="$ld_flags"'

            if not self.is_cross():
                env_vars += ' CC_FOR_BUILD="$cc_build" CXX_FOR_BUILD="$cxx_build"'

            writer.variable("env_vars", env_vars)
            writer.newline()
            writer.variable("root_dir", Path(".").absolute())
            writer.variable("build_dir", "build")
            writer.variable("build_sysroot_dir",
                            "$root_dir/$build_dir/sysroot")
            writer.variable("build_targets_dir", "$build_dir/targets")
            writer.variable("download_dir", "downloads")
            writer.variable("install_dir", self.prefix)
            writer.newline()
            writer.comment("step 1 - download, extract and patch archives")
            writer.newline()

            download_targets = ["binutils", "gcc", "gmp"]

            if self.gcc_with_isl:
                download_targets.append("isl")

            download_targets.extend(["linux", "mpc", "mpfr", "musl"])

            for name in download_targets:
                compression = "xz"

                if name in ["mpc", "musl"]:
                    compression = "gz"

                writer.variable(
                    f"{name}_tarball",
                    f"$download_dir/{name}-${name}_version.tar.{compression}",
                )

            writer.newline()

            for name in download_targets:
                writer.variable(
                    f"{name}_dir", f"$build_dir/{name}-${name}_version")

            writer.newline()
            writer.rule(
                "download-tarball",
                "$download_command $out $url",
                description="Downloading $url",
            )
            writer.newline()
            writer.rule(
                "extract-tar",
                "rm -rf $extracted_dir && tar -C $build_dir -x -$compression -f $in && cd $extracted_dir && $patch_command && touch ../../$out",
                description="Extracting $in",
            )
            writer.newline()
            writer.build(
                "$binutils_tarball",
                "download-tarball",
                pool="console",
                variables={
                    "url": "$gnu_site/binutils/binutils-$binutils_version.tar.xz"
                },
            )
            writer.newline()
            writer.build(
                "$gcc_tarball",
                "download-tarball",
                pool="console",
                variables={
                    "url": "$gnu_site/gcc/gcc-$gcc_version/gcc-$gcc_version.tar.xz"
                },
            )
            writer.newline()
            writer.build(
                "$gmp_tarball",
                "download-tarball",
                pool="console",
                variables={"url": "$gnu_site/gmp/gmp-$gmp_version.tar.xz"},
            )

            if self.gcc_with_isl:
                writer.newline()
                writer.build(
                    "$isl_tarball",
                    "download-tarball",
                    pool="console",
                    variables={"url": "$isl_site/isl-$isl_version.tar.xz"},
                )

            writer.newline()
            writer.build(
                "$linux_tarball",
                "download-tarball",
                pool="console",
                variables={"url": "$linux_site/linux-$linux_version.tar.xz"},
            )
            writer.newline()
            writer.build(
                "$mpc_tarball",
                "download-tarball",
                pool="console",
                variables={"url": "$gnu_site/mpc/mpc-$mpc_version.tar.gz"},
            )
            writer.newline()
            writer.build(
                "$mpfr_tarball",
                "download-tarball",
                pool="console",
                variables={"url": "$gnu_site/mpfr/mpfr-$mpfr_version.tar.xz"},
            )
            writer.newline()
            writer.build(
                "$musl_tarball",
                "download-tarball",
                pool="console",
                variables={
                    "url": "$musl_site/releases/musl-$musl_version.tar.gz"},
            )
            writer.newline()

            name_version_tuples = [
                ("binutils", self.binutils_version),
                ("gcc", self.gcc_version),
                ("gmp", self.gmp_version),
            ]

            if self.gcc_with_isl:
                name_version_tuples.append(("isl", self.isl_version))

            name_version_tuples.extend([
                ("linux", self.linux_version),
                ("mpc", self.mpc_version),
                ("mpfr", self.mpfr_version),
                ("musl", self.musl_version),
            ])

            for (name, version) in name_version_tuples:
                compression = "J"
                patch_command = "true"

                if name in ["mpc", "musl"]:
                    compression = "x"

                if not self.no_patches:
                    patch = Patch(name, version)

                    if patch.exists():
                        patch_command = "patch -p 1"

                        for patch_file in patch.files():
                            patch_command += f" -i ../../{patch_file}"

                        if patch_command == "patch -p 1":
                            patch_command = "true"

                writer.build(
                    f"$build_targets_dir/extract-{name}",
                    f"extract-tar",
                    inputs=[f"${name}_tarball"],
                    variables={
                        "compression": compression,
                        "extracted_dir": f"${name}_dir",
                        "patch_command": patch_command,
                    },
                )
                writer.newline()

            writer.comment("step 2 - build binutils")
            writer.newline()
            writer.variable("binutils_dir", "$build_dir/binutils-build")
            writer.newline()
            writer.rule(
                "configure-binutils",
                f'rm -rf $binutils_dir && mkdir $binutils_dir && cd $binutils_dir && $env_vars ../binutils-$binutils_version/configure {" ".join(self.binutils_flags)} && touch ../../$out',
                description="Configuring binutils $binutils_version",
            )
            writer.newline()
            writer.rule(
                "build-binutils",
                "cd $binutils_dir && $env_vars $make_command all && touch ../../$out",
                description="Building binutils $binutils_version",
            )
            writer.newline()
            writer.rule(
                "install-binutils",
                "cd $binutils_dir && $env_vars $make_command install DESTDIR=$install_dir && touch ../../$out",
                description="Installing binutils $binutils_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/configure-binutils",
                "configure-binutils",
                implicit=["$build_targets_dir/extract-binutils"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-binutils",
                "build-binutils",
                implicit=["$build_targets_dir/configure-binutils"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-binutils",
                "install-binutils",
                implicit=["$build_targets_dir/build-binutils"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 3 - configure gcc")
            writer.newline()
            writer.rule(
                "move-directory",
                f"rm -rf $dst_dir && mv $src_dir $dst_dir && touch $out",
                description="Moving $src_dir -> $dst_dir",
            )

            move_targets = ["gmp"]

            if self.gcc_with_isl:
                move_targets.append("isl")

            move_targets.extend(["mpc", "mpfr"])
            build_targets_move = []

            for name in move_targets:
                writer.newline()
                writer.build(
                    f"$build_targets_dir/move-{name}",
                    "move-directory",
                    implicit=[f"$build_targets_dir/extract-{name}"],
                    variables={
                        "src_dir": f"${name}_dir",
                        "dst_dir": f"$gcc_dir/{name}",
                    },
                )
                build_targets_move.append(f"$build_targets_dir/move-{name}")

            writer.newline()
            writer.variable("gcc_dir", "$build_dir/gcc-build")
            writer.newline()

            gcc_vars = []

            if self.is_cross():
                gcc_vars.extend(
                    [
                        'AR_FOR_TARGET="$root_dir/$binutils_dir/binutils/ar"',
                        'AS_FOR_TARGET="$root_dir/$binutils_dir/gas/as-new"',
                        'LD_FOR_TARGET="$root_dir/$binutils_dir/ld/ld-new"',
                        'NM_FOR_TARGET="$root_dir/$binutils_dir/binutils/nm-new"',
                        'OBJCOPY_FOR_TARGET="$root_dir/$binutils_dir/binutils/objcopy"',
                        'OBJDUMP_FOR_TARGET="$root_dir/$binutils_dir/binutils/objdump"',
                        'RANLIB_FOR_TARGET="$root_dir/$binutils_dir/binutils/ranlib"',
                        'READELF_FOR_TARGET="$root_dir/$binutils_dir/binutils/readelf"',
                        'STRIP_FOR_TARGET="$root_dir/$binutils_dir/binutils/strip-new"',
                    ]
                )

            writer.rule(
                "configure-gcc",
                f'rm -rf $build_sysroot_dir && mkdir -p $build_sysroot_dir/usr/include && rm -rf $gcc_dir && mkdir $gcc_dir && cd $gcc_dir && $env_vars ../gcc-$gcc_version/configure {" ".join(self.gcc_flags)} {" ".join(gcc_vars)} && touch ../../$out',
                description="Configuring gcc $gcc_version",
            )
            writer.newline()

            implicit = ["$build_targets_dir/extract-gcc"]
            implicit.extend(build_targets_move)
            implicit.append("$build_targets_dir/build-binutils")

            writer.build(
                "$build_targets_dir/configure-gcc",
                "configure-gcc",
                implicit=implicit,
                pool="console",
            )
            writer.newline()
            writer.comment("step 4 - build gcc (all-gcc)")
            writer.newline()
            writer.rule(
                "build-gcc-all-gcc",
                "cd $gcc_dir && $env_vars $make_command all-gcc && touch ../../$out",
                description="Building gcc $gcc_version (all-gcc)",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-gcc-all-gcc",
                "build-gcc-all-gcc",
                implicit=["$build_targets_dir/configure-gcc"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 5 - configure musl")
            writer.newline()
            writer.variable("musl_dir", "$build_dir/musl-build")

            musl_configure_env_vars = [
                'LIBCC="$root_dir/$gcc_dir/$target/libgcc/libgcc.a"'
            ]
            musl_flags = [
                "--prefix=",
                "--host=$target",
            ]
            musl_vars = []

            if self.is_cross():
                host_exe_suffix = ".exe" if platform.system() == "Windows" else ""
                musl_configure_env_vars.append(
                    f'CC="$root_dir/$gcc_dir/gcc/xgcc{host_exe_suffix} -B $root_dir/$gcc_dir/gcc"'
                )
                musl_vars.extend(
                    [
                        'AR="$root_dir/$binutils_dir/binutils/ar"',
                        'RANLIB="$root_dir/$binutils_dir/binutils/ranlib"',
                    ]
                )
            else:
                musl_configure_env_vars.extend(
                    [
                        "CC=${target}-gcc",
                        "CROSS_COMPILE=${target}-",
                    ]
                )
                musl_vars.extend(
                    [
                        "AR=${target}-ar",
                        "RANLIB=${target}-ranlib",
                    ]
                )

            # use cache
            writer.newline()
            writer.rule(
                "configure-musl",
                f'rm -rf $musl_dir && mkdir $musl_dir && cd $musl_dir && $env_vars {" ".join(musl_configure_env_vars)} ../musl-$musl_version/configure {" ".join(musl_flags)} && touch ../../$out',
                description="Configuring musl $musl_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/configure-musl",
                "configure-musl",
                implicit=[
                    "$build_targets_dir/extract-musl",
                    "$build_targets_dir/build-gcc-all-gcc",
                ],
                pool="console",
            )
            writer.newline()
            writer.comment("step 6 - install musl (headers)")
            writer.newline()
            writer.rule(
                "install-musl-headers-dep",
                f"cd $musl_dir && $env_vars $make_command install-headers prefix=/usr DESTDIR=$build_sysroot_dir && touch ../../$out",
                description="Configuring musl $musl_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-musl-headers-dep",
                "install-musl-headers-dep",
                implicit=["$build_targets_dir/configure-musl"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 7 - build gcc (libgcc.a)")
            writer.newline()
            writer.rule(
                "build-gcc-libgcc-static",
                "cd $gcc_dir && $env_vars $make_command enable_shared=no all-target-libgcc && touch ../../$out",
                description="Building gcc $gcc_version (libgcc.a)",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-gcc-libgcc-static",
                "build-gcc-libgcc-static",
                implicit=["$build_targets_dir/install-musl-headers-dep"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 8 - build musl")
            writer.newline()
            writer.rule(
                "build-musl",
                f'cd $musl_dir && $env_vars $make_command {" ".join(musl_vars)} && touch ../../$out',
                description="Building musl $musl_version",
            )
            writer.newline()
            writer.rule(
                "install-musl-dep",
                "cd $musl_dir && $env_vars $make_command install prefix=/usr DESTDIR=$build_sysroot_dir && touch ../../$out",
                description="Installing musl $musl_version at $build_sysroot_dir",
            )
            writer.newline()
            writer.rule(
                "install-musl",
                "cd $musl_dir && $env_vars $make_command install DESTDIR=$install_dir/$target && touch ../../$out",
                description="Installing musl $musl_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-musl",
                "build-musl",
                implicit=["$build_targets_dir/build-gcc-libgcc-static"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-musl-dep",
                "install-musl-dep",
                implicit=["$build_targets_dir/build-musl"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-musl",
                "install-musl",
                implicit=["$build_targets_dir/build-musl"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 9 - build gcc (libgcc.so)")
            writer.newline()
            writer.rule(
                "clean-gcc-libgcc-static",
                "cd $gcc_dir && $env_vars $make_command -C $target/libgcc distclean && touch ../../$out",
                description="Cleaning gcc $gcc_version (libgcc.a)",
            )
            writer.newline()
            writer.rule(
                "build-gcc-libgcc-shared",
                "cd $gcc_dir && $env_vars $make_command enable_shared=yes all-target-libgcc && touch ../../$out",
                description="Building gcc $gcc_version (libgcc.so)",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/clean-gcc-libgcc-static",
                "clean-gcc-libgcc-static",
                implicit=["$build_targets_dir/install-musl-dep"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-gcc-libgcc-shared",
                "build-gcc-libgcc-shared",
                implicit=["$build_targets_dir/clean-gcc-libgcc-static"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 10 - build gcc")
            writer.newline()
            writer.rule(
                "build-gcc",
                "cd $gcc_dir && $env_vars $make_command && touch ../../$out",
                description="Building gcc $gcc_version",
            )
            writer.newline()
            writer.rule(
                "install-gcc",
                "cd $gcc_dir && $env_vars $make_command install DESTDIR=$install_dir && touch ../../$out",
                # ln -sf $(TARGET)-gcc $(DESTDIR)$(OUTPUT)/bin/$(TARGET)-cc
                description="Installing gcc $gcc_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-gcc",
                "build-gcc",
                implicit=["$build_targets_dir/build-gcc-libgcc-shared"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-gcc",
                "install-gcc",
                implicit=["$build_targets_dir/build-gcc"],
                pool="console",
            )
            writer.newline()
            writer.comment("step 11 - install linux (headers)")
            writer.newline()
            
            # https://git.musl-libc.org/cgit/musl/tree/INSTALL
            # https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/arch
            arch = self.target.split("-")[0]
            
            if arch.startswith("aarch64"):
            	arch = "arm64"
            elif arch.startswith("arm"):
                arch = "arm"
            elif arch.startswith("i") and arch.endswith("86"):
            	arch = "x86"
            elif arch.startswith("microblaze"):
                arch = "microblaze"
            elif arch.startswith("mips"):
                arch = "mips"
            elif arch.startswith("or1k"):
                arch = "openrisc"
            elif arch.startswith("powerpc"):
                arch = "powerpc"
            elif arch.startswith("riscv"):
                arch = "riscv"
            elif arch.startswith("s390"):
                arch = "s390"
            elif arch.startswith("s390"):
                arch = "s390"
            elif arch.startswith("sh"):
                arch = "sh"
            elif arch.startswith("x86_64"):
                arch = "x86_64"

            writer.variable("arch", arch)
            writer.newline()
            writer.rule(
                "build-linux",
                "cd $linux_dir && $env_vars $make_command mrproper ARCH=$arch && touch ../../$out",
                description="Building linux $linux_version",
            )
            writer.newline()
            writer.rule(
                "install-linux",
                "rm -rf $build_dir/linux-build && mkdir $build_dir/linux-build && cd $linux_dir && $env_vars $make_command headers_install O=$root_dir/$build_dir/linux-build ARCH=$arch INSTALL_HDR_PATH=$install_dir/$target && touch ../../$out",
                description="Installing linux $linux_version",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/build-linux",
                "build-linux",
                implicit=["$build_targets_dir/extract-linux"],
                pool="console",
            )
            writer.newline()
            writer.build(
                "$build_targets_dir/install-linux",
                "install-linux",
                implicit=["$build_targets_dir/build-linux"],
                pool="console",
            )
            writer.newline()
            writer.comment("clean targets")
            writer.newline()
            writer.rule("delete-directory", "rm -rf $in",
                        description="Deleting $in")
            writer.newline()
            writer.rule("clean-all", "true", description="Cleaned everything")
            writer.newline()
            writer.build(
                "clean-build",
                "delete-directory",
                inputs=["$build_dir"],
            )
            writer.build(
                "clean-downloads",
                "delete-directory",
                inputs=["$download_dir"],
            )
            writer.build(
                "clean",
                "clean-all",
                implicit=["clean-build", "clean-downloads"],
            )
            writer.newline()
            writer.comment("install targets")
            writer.newline()
            writer.rule(
                "install-all", "true", description="Installed toolchain at $install_dir"
            )
            writer.newline()
            writer.build(
                "install",
                "install-all",
                implicit=[
                    "$build_targets_dir/install-binutils",
                    "$build_targets_dir/install-gcc",
                    "$build_targets_dir/install-musl",
                    "$build_targets_dir/install-linux",
                ],
            )
            writer.newline()
            writer.comment("default targets")
            writer.newline()
            writer.default(
                [
                    "$build_targets_dir/build-gcc",
                    "$build_targets_dir/build-linux",
                ]
            )


class Patch:
    name = str,
    version = str,

    path = str,

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version

        self.path = f"patches/musl-cross-make-{MUSL_CROSS_MAKE_COMMIT}/patches/{name}-{version}"

    def exists(self) -> bool:
        return Path(self.path).exists()

    def files(self) -> List[str]:
        return [f"{self.path}/{i}" for i in os.listdir(self.path)]


def main(args: argparse.Namespace) -> None:
    args = Args(args)

    if not args.no_patches:
        extracted_dir = f"patches/musl-cross-make-{MUSL_CROSS_MAKE_COMMIT}"

        if not Path(extracted_dir).exists():
            url = f"https://github.com/richfelker/musl-cross-make/archive/{MUSL_CROSS_MAKE_COMMIT}.zip"
            print(f"Downloading patches from {url}")
            response = requests.get(url)
            data = io.BytesIO(response.content)
            print(f"Extracting patches at {extracted_dir}")
            f = zipfile.ZipFile(data)
            f.extractall("patches")
            f.close()
        else:
            print(f"Patches are already downloaded at {extracted_dir}")

    failed = args.try_get_tools()

    if failed:
        print("Error: Some tools do not exist. You may need add them to your PATH.")
        sys.exit(1)

    args.dependencies_summary()
    args.ninja()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="setup",
        description="Configure musl cross toolchain.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--no-patches",
        action="store_true",
        default=False,
        help="Do not apply patches from richfelker/musl-cross-make.",
    )
    parser.add_argument(
        "--prefix",
        default="$root_dir/toolchain",
        help="Directory where to install toolchain.",
    )
    group = parser.add_argument_group("toolchain options")
    group.add_argument(
        "--host",
        help="Host for the toolchain. Do not use this flag until you need to build cross native or canadian cross toolchain.",
    )
    group.add_argument(
        "--target",
        required=True,
        help="Target for the toolchain.",
    )
    group = parser.add_argument_group("compiler options")
    group.add_argument(
        "--cc",
        default=f"$host-gcc",
        help="C compiler for host.",
    )
    group.add_argument(
        "--cxx",
        default=f"$host-g++",
        help="C++ compiler for host.",
    )
    group.add_argument(
        "--cc-build",
        default=f"gcc",
        help="C compiler for build.",
    )
    group.add_argument(
        "--cxx-build",
        default=f"g++",
        help="C++ compiler for build.",
    )
    group.add_argument(
        "--cc-flags",
        default=None,
        help="Extra C compiler flags.",
    )
    group.add_argument(
        "--cxx-flags",
        default=None,
        help="Extra C++ compiler flags.",
    )
    group.add_argument(
        "--ld-flags",
        default=None,
        help="Extra linker flags.",
    )
    group.add_argument(
        "--enable-cache",
        action="store_true",
        default=False,
        help="Use ccache or sccache (if available) as compiler wrapper.",
    )
    group = parser.add_argument_group("configure options")
    group.add_argument(
        "--binutils-flags",
        default=None,
        help="Add extra flags when configuring binutils.",
    )
    group.add_argument(
        "--gcc-flags",
        default=None,
        help="Add extra flags when configuring gcc.",
    )
    group.add_argument(
        "--gcc-with-isl",
        action="store_true",
        default=False,
        help="Build gcc with isl support.",
    )
    group = parser.add_argument_group("dependencies")
    group.add_argument(
        "--binutils-version",
        default="2.40",  # https://ftp.gnu.org/gnu/binutils
        help="Binutils version to build.",
    )
    group.add_argument(
        "--gcc-version",
        default="13.1.0",  # https://ftp.gnu.org/gnu/gcc
        help="Gcc version to build.",
    )
    group.add_argument(
        "--gmp-version",
        default="6.2.1",  # https://ftp.gnu.org/gnu/gmp
        help="Gmp version to build.",
    )
    group.add_argument(
        "--mpc-version",
        default="1.3.1",  # https://ftp.gnu.org/gnu/mpc
        help="Mpc version to build.",
    )
    group.add_argument(
        "--mpfr-version",
        default="4.2.0",  # https://ftp.gnu.org/gnu/mpfr
        help="Mpfr version to build.",
    )
    group.add_argument(
        "--isl-version",
        default="0.24",  # https://libisl.sourceforge.io
        # default="0.26",
        help="Isl version to build.",
    )
    group.add_argument(
        "--linux-version",
        default="6.3.5",  # https://www.kernel.org
        help="Linux version to build.",
    )
    group.add_argument(
        "--musl-version",
        default="1.2.4",  # https://musl.libc.org
        help="Musl version to build.",
    )
    main(parser.parse_args())
