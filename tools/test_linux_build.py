"""Linux integration check: native shader generator and a tiny Wii U RPX, with no game data.

Run with .venv/bin/python tools/test_linux_build.py after installing devkitPPC/wut.
The default check does not need CafeGLSL or AAC. Add --full after installing all dependencies to compile shaders,
link a synthetic ARM program with the complete Wii U runtime, and round-trip a WUP using a random test key.
All generated files stay in a temporary directory and are deleted; this does not produce an installable game.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools/win'))
import build3ds


def checked(argv, **kwargs):
    result = build3ds.run(argv, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} failed:\n{result.stdout}\n{result.stderr}')
    return result.stdout


def check_rpx(rpx):
    data = rpx.read_bytes()
    assert data[:6] == b'\x7fELF\x01\x02'  # ELF32, big-endian
    assert int.from_bytes(data[18:20], 'big') == 20  # PowerPC
    result = subprocess.run([sys.executable, str(ROOT / 'tools/rpx_check.py'), str(rpx),
                             str(ROOT / 'phase0/title_code/code/cos.xml')], capture_output=True, text=True)
    if result.returncode or 'RESULT: no problems found' not in result.stdout:
        raise RuntimeError(result.stdout + result.stderr)


def full_runtime(work, args, shaders):
    game = Path(work, 'synthetic ARM program')
    game.mkdir()
    # mov r0, #42; bx lr. These two test instructions are authored here, with no dumped game data.
    code = struct.pack('<II', 0xE3A0002A, 0xE12FFF1E)
    (game / 'code.bin').write_bytes(code)
    header = bytearray(0x800)
    struct.pack_into('<III', header, 0x10, 0x100000, 1, len(code))
    (game / 'exheader.bin').write_bytes(header)
    (game / 'info.json').write_text(json.dumps({
        'program_id': '0004000000000000',
        'text': {'addr': 0x100000, 'size': len(code)},
        'rodata': {'addr': 0x101000, 'size': 0},
        'data': {'addr': 0x102000, 'size': 0},
    }))
    g = build3ds.Game('linux_smoke', str(game))
    g.out = str(Path(work, 'full-runtime'))
    Path(g.out).mkdir()
    g.rpx, g.nm = g.out + '/linux_smoke.rpx', g.out + '/game.nm'
    build3ds.build_code(g, args)
    check_rpx(Path(g.rpx))
    print('ok     ARM-to-C recompilation and full runtime link with Wii U CafeGLSL/AAC')

    # Exercise title creation and the packer's own decrypt-and-compare verification with synthetic data.
    from make_game_title import make_title
    (game / 'romfs.bin').write_bytes(bytes(0x1000))
    (game / 'exefs').mkdir()
    (game / 'exefs/icon.bin').write_bytes(bytes(0x36C0))
    archives = Path(work, 'empty system archives')
    archives.mkdir()
    title, packed = Path(work, 'test title'), Path(work, 'test WUP')
    make_title(str(title), str(game), g.rpx, str(shaders), '000500003DFFFFFE', 'Linux synthetic test', 'WUP-N-LNXT', str(archives))
    output = checked([sys.executable, str(ROOT / 'phase0/tools/pack_wup.py'), str(title), str(packed)], input=os.urandom(16).hex())
    assert 'round trip verified' in output
    assert (packed / 'title.tmd').is_file() and (packed / 'title.tik').is_file()
    print('ok     synthetic title creation and WUP encryption/decryption round trip')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full', action='store_true', help='also compile shaders, link the full runtime, and pack a synthetic title')
    options = ap.parse_args()
    if not sys.platform.startswith('linux'):
        sys.exit('This integration check is for Linux.')
    if options.full:
        problems = build3ds.HOST.problems()
        if problems:
            sys.exit('Missing build dependencies:\n  ' + '\n  '.join(problems))
    with tempfile.TemporaryDirectory(prefix='3dii Linux build ') as work:
        build3ds.WORK = work
        os.environ['THREEDII_OBJCACHE'] = work + '/objcache'
        args = SimpleNamespace(jobs=2, force=False, extra_flags=None, only=None, opt=None)
        generator = build3ds.build_shadergen(args)
        logs, out = Path(work, 'logs'), Path(work, 'shaders')
        logs.mkdir()
        pipelines = Path(work, 'empty.bin')
        pipelines.touch()
        checked([generator, str(pipelines), str(logs), str(out), '-'])
        generated = (out / 'glsl/list.txt').read_text().split()
        assert any(p.endswith('.vert') for p in generated)
        assert any(p.endswith('.frag') for p in generated)
        assert (out / 'shaders.idx').stat().st_size
        print('ok     native shader generation from empty logs')
        if options.full:
            build3ds.compile_shaders(str(out), args.jobs)
            print('ok     native CafeGLSL shader compilation, merge and register checks')

        build3ds.link_setup()
        source = Path(work, 'minimal.c')
        source.write_text('int main(int argc, char **argv) { return 0; }\n')
        checked([build3ds.PPC('gcc'), '-mcpu=750', '-meabi', '-mhard-float', '-specs=rpx/rpx.specs',
                 str(source), '-Wl,--start-group', '-L' + build3ds.WUT + '/lib', '-lwut', '-lm', '-lc',
                 '-lsysbase', '-lgcc', '-Wl,--end-group', '-o', 'minimal.elf'], cwd=work)
        checked([build3ds.HOST.elf2rpl, 'minimal.elf', 'minimal.rpx'], cwd=work)
        rpx = Path(work, 'minimal.rpx')
        check_rpx(rpx)
        print('ok     devkitPPC compile, custom linker script, elf2rpl and RPX checks')
        if options.full:
            full_runtime(work, args, out)


if __name__ == '__main__':
    main()
