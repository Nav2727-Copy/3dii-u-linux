"""Build a runnable source bundle for Ubuntu 26.04 x86-64: python builder/make_linux_zip.py."""
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / 'dist'
NAME = '3Dii U Builder Linux'
SDK = Path('/opt/devkitpro')


def copy_tree(src, dst, *, ignore=()):
    if not Path(src).is_dir():
        raise SystemExit(f'required folder is missing: {src}')
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))


def copy_file(src, dst, *, executable=False):
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise SystemExit(f'required file is missing: {src}')
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if executable:
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_launcher(bundle):
    (bundle / 'run-builder.sh').write_text('''#!/bin/sh
set -eu
BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DEVKITPRO="$BUNDLE_DIR/toolchain/devkitpro"
export DEVKITPPC="$DEVKITPRO/devkitPPC"
export THREEDII_DEVKITPRO="$DEVKITPRO"
export THREEDII_WORK="${XDG_DATA_HOME:-$HOME/.local/share}/3Dii U Builder"
export PYTHONDONTWRITEBYTECODE=1
exec "$BUNDLE_DIR/.venv/bin/python" "$BUNDLE_DIR/builder/app.py" "$@"
''')
    path = bundle / 'run-builder.sh'
    path.chmod(path.stat().st_mode | 0o111)


def stage(bundle):
    # Package only source and compiler inputs. The CIAs, OTP, key files, decrypted archives,
    # generated game code, and WUPs in the developer's checkout never enter the bundle.
    shutil.copytree(ROOT / 'builder', bundle / 'builder', ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'make_app.py', 'make_linux_zip.py', 'test_*.py', 'selftest.py',
        'dist', 'build_app'))
    for name in ('tools', 'phase0', 'port'):
        shutil.copytree(ROOT / name, bundle / name, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', 'console_*', 'build', 'dist', 'results', 'title_gpu'))
    copy_tree(ROOT / 'third_party/3dsrecomp', bundle / 'third_party/3dsrecomp', ignore=(
        '__pycache__', '*.pyc', 'games', '.git'))
    copy_file(ROOT / 'third_party/cafeglsl/glslcompiler', bundle / 'third_party/cafeglsl/glslcompiler', executable=True)
    copy_tree(ROOT / 'third_party/cafeglsl_ppc/cafeglsl-1.0.0-rc1-wiiu-ppc',
              bundle / 'third_party/cafeglsl_ppc/cafeglsl-1.0.0-rc1-wiiu-ppc')
    copy_tree(ROOT / 'toolchain/aac_ppc', bundle / 'toolchain/aac_ppc')
    for name in ('LICENSE', 'THIRD_PARTY_LICENSES.md'):
        copy_file(ROOT / name, bundle / name)

    if not (Path(sys.prefix) / 'pyvenv.cfg').is_file():
        raise SystemExit('run this script with the project virtual environment: .venv/bin/python builder/make_linux_zip.py')
    copy_tree(Path(sys.prefix), bundle / '.venv', ignore=('__pycache__', '*.pyc', 'build', 'dist', 'tests'))

    devkit = bundle / 'toolchain/devkitpro'
    for name in ('devkitPPC', 'wut', 'tools'):
        copy_tree(SDK / name, devkit / name, ignore=('__pycache__', '*.pyc', 'share/doc', 'share/man', 'share/info'))
    copy_file(ROOT / 'builder/bin/makerom', bundle / 'builder/bin/makerom', executable=True)
    make_launcher(bundle)
    (bundle / 'README-Linux.txt').write_text(f'''3Dii U Builder {__import__('credits').VERSION} for Linux

This ZIP was built on Ubuntu 26.04 for x86-64. It includes the builder, its Python
packages, and the Wii U build toolchain. It needs an x86-64 Linux system with glibc
2.39 or newer and Python 3.14 available at /usr/bin/python3.14 (the Python virtual
environment in the ZIP uses that interpreter). It is not a single-file executable.

To run, extract the whole ZIP, open a terminal in the extracted folder and run:

  ./run-builder.sh

For your first build, choose a base game with no update or mods. Under Your files,
choose your own Wii U otp.bin and a folder containing your own 3DS 0004009b system
archives (the shared font and Mii data). For an encrypted dump, also supply your own
3DS boot9.bin and any seeddb.bin it requires. No games, keys, or console archives
are included in this package. The output can go to a folder or a mounted SD card.

Read builder/README.md for Linux setup, command-line usage, and troubleshooting.
Install the WUP with WUP Installer GX2 on your configured Wii U. See README.md for
the Wii U requirements and game compatibility information.
''')


def main():
    if sys.platform != 'linux' or sys.maxsize < 2**32 or os.uname().machine not in ('x86_64', 'amd64'):
        raise SystemExit('this bundle script currently targets Linux x86-64')
    required = [SDK / 'devkitPPC/bin/powerpc-eabi-gcc', SDK / 'wut/share/wut.ld',
                SDK / 'tools/bin/elf2rpl', ROOT / 'third_party/cafeglsl/glslcompiler',
                ROOT / 'third_party/cafeglsl_ppc/cafeglsl-1.0.0-rc1-wiiu-ppc/lib/libcafeglsl.a',
                ROOT / 'toolchain/aac_ppc/lib/libavcodec.a', ROOT / 'builder/bin/makerom']
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise SystemExit('install the Linux build dependencies first:\n  ' + '\n  '.join(missing))
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='3dii-linux-package-') as tmp:
        bundle = Path(tmp) / NAME
        bundle.mkdir()
        stage(bundle)
        archive = OUT / f'{NAME}.zip'
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1,
                             allowZip64=True) as z:
            for folder, _, files in os.walk(bundle):
                for filename in files:
                    path = Path(folder) / filename
                    z.write(path, path.relative_to(bundle.parent))
    print(f'{archive} ({archive.stat().st_size >> 20} MB)')


if __name__ == '__main__':
    main()
