"""Host tools shared by the Windows and Linux builders. No environment changes on import."""
import ctypes
import os
import shutil
import subprocess
import sys


def executable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


class Toolchain:
    def __init__(self, root, environ=None, platform=None):
        self.env = dict(os.environ if environ is None else environ)
        self.platform = sys.platform if platform is None else platform
        self.win = self.platform == 'win32'
        self.suffix = '.exe' if self.win else ''
        self.root = str(root).replace('\\', '/')

        def path(key, default):
            return os.path.expanduser(self.env.get(key) or default).replace('\\', '/')

        self.dkp = path('THREEDII_DEVKITPRO', self.env.get('DEVKITPRO') or
                        ('C:/devkitPro/opt/devkitpro' if self.win else '/opt/devkitpro'))
        # A bundled toolchain takes precedence over a developer's DEVKITPPC.
        self.ppc = self.dkp + '/devkitPPC' if self.env.get('THREEDII_DEVKITPRO') else path('DEVKITPPC', self.dkp + '/devkitPPC')
        self.wut = self.dkp + '/wut'
        self.glslc = self.find('THREEDII_GLSLC', self.root + '/third_party/cafeglsl/glslcompiler' + self.suffix, 'glslcompiler' + self.suffix)
        self.cafeglsl_ppc = path('THREEDII_CAFEGLSL_PPC', self.root + '/third_party/cafeglsl_ppc/cafeglsl-1.0.0-rc1-wiiu-ppc')
        self.aac = path('THREEDII_AAC', self.root + '/toolchain/aac_ppc')
        self.shadergen = self.env.get('THREEDII_SHADERGEN')
        self.llvm = 'C:/Program Files/LLVM/bin/'
        self.mingw = 'C:/msys64/mingw64'
        if self.win:
            self.host_cxx = [self.llvm + 'clang++.exe', '--target=x86_64-w64-mingw32', '--sysroot=' + self.mingw, '-fuse-ld=lld']
        else:
            cxx = self.env.get('THREEDII_CXX') or self.env.get('CXX') or 'c++'
            self.host_cxx = [shutil.which(cxx, path=self.env.get('PATH', '')) or cxx]
        self.elf2rpl = self.dkp + '/tools/bin/elf2rpl' + self.suffix

    def find(self, key, bundled, name):
        if self.env.get(key):
            value = os.path.expanduser(self.env[key])
            return shutil.which(value, path=self.env.get('PATH', '')) or value
        return bundled if os.path.isfile(bundled) else shutil.which(name, path=self.env.get('PATH', '')) or bundled

    def ppc_tool(self, name):
        return self.ppc + '/bin/powerpc-eabi-' + name + self.suffix

    def environment(self):
        env = dict(self.env, DEVKITPRO=self.dkp, DEVKITPPC=self.ppc)
        sep = ';' if self.win else ':'
        env['PATH'] = sep.join([self.ppc + '/bin', self.dkp + '/tools/bin', env.get('PATH', '')])
        return env

    def checks(self, code=True, shaders=True):
        """(description, present, remedy), also used by setup.py --check before opening any dump."""
        rows = [('Host platform', self.win or self.platform.startswith('linux'), 'use Windows or Linux')]
        if code:
            install = 'install devkitPPC, wut and wut-tools; set DEVKITPRO if outside the default location'
            for name in ('gcc', 'g++', 'nm', 'size', 'objdump'):
                tool = self.ppc_tool(name)
                rows.append((f'devkitPPC {name}', executable(tool), f'{install}: {tool}'))
            rows.append(('elf2rpl', executable(self.elf2rpl), f'{install}: {self.elf2rpl}'))
            files = ('include/coreinit/thread.h', 'share/wut.ld', 'share/wut.specs', 'lib/libwut.a')
            rows.append(('wut SDK', all(os.path.isfile(self.wut + '/' + f) for f in files), install))
            try:
                with open(self.wut + '/include/coreinit/thread.h', encoding='utf-8') as f:
                    current = 'OSThreadEntryPointFn)(int argc, void *argv)' in f.read()
            except OSError:
                current = False
            rows.append(('wut thread API (1.9.2 or newer)', current, 'update wut with devkitPro pacman'))
            for label, folder, files in (
                ('CafeGLSL Wii U library', self.cafeglsl_ppc, ('include/cafeglsl/CafeGLSLCompiler.h', 'lib/libcafeglsl.a')),
                ('Wii U AAC libraries', self.aac, ('include/libavcodec/avcodec.h', 'include/libavutil/avutil.h', 'lib/libavcodec.a', 'lib/libavutil.a')),
            ):
                rows.append((label, all(os.path.isfile(folder + '/' + f) for f in files),
                             f'install the project dependency bundle at {folder}; see builder/README.md'))
        if shaders:
            rows.append(('CafeGLSL v0.2.0 host compiler', executable(self.glslc),
                         f'install the native v0.2.0 compiler at {self.glslc}, or set THREEDII_GLSLC'))
            compiler = self.shadergen or self.host_cxx[0]
            rows.append(('Shader generator' if self.shadergen else 'Host C++ compiler', executable(compiler),
                         f'provide {compiler}; on Linux set CXX to a C++20 compiler, or THREEDII_SHADERGEN to a built generator'))
        return rows

    def problems(self, **stages):
        return [f'{name}: {remedy}' for name, good, remedy in self.checks(**stages) if not good]


def free_mb():
    if sys.platform == 'win32':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in ('total', 'avail', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'avail_extended')]]
        m = MemoryStatus()
        m.length = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.avail >> 20
        return 0  # run_jobs will still allow one compiler at a time
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def run(argv, **kwargs):
    if sys.platform == 'win32':
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4000  # BELOW_NORMAL_PRIORITY_CLASS
    else:
        # Avoid preexec_fn: the builder starts compilers from several Python threads.
        nice = shutil.which('nice')
        if nice:
            argv = [nice, '-n', '10', *argv]
    return subprocess.run(argv, **kwargs)
