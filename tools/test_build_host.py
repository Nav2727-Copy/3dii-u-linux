"""Host configuration and subprocess regression checks; no SDK or game files needed."""
import os
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import build_host


class HostTests(unittest.TestCase):
    def test_linux_toolchain_without_shell_profile(self):
        host = build_host.Toolchain('/project', environ={'PATH': '/usr/bin:/bin'}, platform='linux')
        self.assertEqual(host.ppc_tool('gcc'), '/opt/devkitpro/devkitPPC/bin/powerpc-eabi-gcc')
        self.assertEqual(host.environment()['PATH'].split(':')[:2],
                         ['/opt/devkitpro/devkitPPC/bin', '/opt/devkitpro/tools/bin'])
        self.assertNotIn('--target=x86_64-w64-mingw32', host.host_cxx)

    def test_windows_defaults_are_preserved(self):
        host = build_host.Toolchain('C:/project', environ={'PATH': 'C:/Windows'}, platform='win32')
        self.assertEqual(host.ppc_tool('gcc'), 'C:/devkitPro/opt/devkitpro/devkitPPC/bin/powerpc-eabi-gcc.exe')
        self.assertEqual(host.environment()['PATH'].split(';')[-1], 'C:/Windows')
        self.assertIn('--target=x86_64-w64-mingw32', host.host_cxx)
        self.assertTrue(host.elf2rpl.endswith('.exe'))

    def test_environment_overrides_and_bundle_priority(self):
        env = {'DEVKITPRO': '/custom/sdk', 'DEVKITPPC': '/custom/compiler', 'PATH': ''}
        host = build_host.Toolchain('/project', environ=env, platform='linux')
        self.assertEqual(host.ppc_tool('gcc'), '/custom/compiler/bin/powerpc-eabi-gcc')
        host = build_host.Toolchain('/project', environ=dict(env, THREEDII_DEVKITPRO='/app/engine/devkitpro'), platform='linux')
        self.assertEqual(host.ppc_tool('gcc'), '/app/engine/devkitpro/devkitPPC/bin/powerpc-eabi-gcc')
        self.assertEqual(env['DEVKITPRO'], '/custom/sdk')

    def test_bundled_tools_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory(prefix='host tools ') as root:
            exe = Path(root, 'third_party/cafeglsl/glslcompiler')
            exe.parent.mkdir(parents=True)
            exe.touch()
            exe.chmod(0o755)
            host = build_host.Toolchain(root, environ={'PATH': ''}, platform='linux')
            self.assertEqual(host.glslc, str(exe))
            self.assertFalse(any('CafeGLSL v0.2.0 host' in p for p in host.problems(code=False)))
            host = build_host.Toolchain(root, environ={'PATH': '', 'THREEDII_GLSLC': '/missing/compiler'}, platform='linux')
            self.assertEqual(host.glslc, '/missing/compiler')
            self.assertTrue(any('CafeGLSL v0.2.0 host' in p for p in host.problems(code=False)))

    def test_prebuilt_shadergen_needs_no_cxx(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root, 'tool')
            exe.touch()
            exe.chmod(0o755)
            host = build_host.Toolchain(root, environ={'PATH': '', 'THREEDII_SHADERGEN': str(exe), 'THREEDII_GLSLC': str(exe)}, platform='linux')
            self.assertEqual(host.problems(code=False), [])
            self.assertTrue(any('Wii U AAC' in p for p in host.problems()))

    def test_windows_priority_keeps_existing_flags(self):
        with patch.object(build_host.sys, 'platform', 'win32'), patch.object(build_host.subprocess, 'run') as run:
            build_host.run(['compiler.exe'], creationflags=0x08000000)
            self.assertEqual(run.call_args.kwargs['creationflags'], 0x08004000)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux process test')
    def test_linux_process_handles_spaces_and_stdin(self):
        with tempfile.TemporaryDirectory(prefix='host process ') as folder:
            script = Path(folder, 'child with spaces.py')
            script.write_text('import sys; print(sys.stdin.read() + sys.argv[1])')
            result = build_host.run([sys.executable, str(script), 'argument with spaces'],
                                    input='input ', capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'input argument with spaces')
            self.assertGreater(build_host.free_mb(), 0)

    def test_pipeline_reports_dependencies_before_reading_game(self):
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ, THREEDII_DEVKITPRO='/nonexistent/sdk', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, str(root / 'builder/pipeline.py'), '--game', '/missing/game.3ds',
                                 '--otp', '/missing/otp.bin', '--out', '/unused/output'],
                                cwd=root, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing build dependencies', result.stdout)
        self.assertIn('devkitPPC gcc', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_dependency_archive_is_verified_and_imports_only_target_libraries(self):
        spec = importlib.util.spec_from_file_location('builder_setup', Path(__file__).with_name('setup.py'))
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp, 'deps.zip')
            root = Path(tmp, 'project')
            root.mkdir()
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('toolchain/aac_ppc/lib/libavcodec.a', b'test AAC library')
                z.writestr('third_party/cafeglsl_ppc/compiler/lib/libcafeglsl.a', b'test shader library')
                z.writestr('builder/bin/makerom.exe', b'Windows program')
            with patch.object(setup, 'ROOT', str(root)):
                with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                    setup.import_target_libraries(str(archive))
                self.assertEqual(list(root.iterdir()), [])
                with patch.object(setup, 'DEPS_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest()):
                    setup.import_target_libraries(str(archive))
                self.assertEqual((root / 'toolchain/aac_ppc/lib/libavcodec.a').read_bytes(), b'test AAC library')
                self.assertFalse((root / 'builder').exists())


if __name__ == '__main__':
    unittest.main()
