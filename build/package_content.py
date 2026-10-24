"""Select application and native runtime files for an installed release."""
from pathlib import PurePosixPath


DEVELOPMENT_DIRECTORIES = frozenset({
    '__pycache__', 'test', 'tests', 'testing', 'benchmark', 'benchmarks',
    'example', 'examples', 'demo', 'demos', 'doc', 'docs', 'documentation',
    'include', 'includes', 'cmake', 'pkgconfig', 'mkspecs', 'aclocal',
    'man', 'gdb', 'lldb', 'valgrind', 'meson', 'vala', 'typesystems',
    'qttest', 'drawresources',
})
DEVELOPMENT_SUFFIXES = frozenset({
    '.pyc', '.pyo', '.pdb', '.lib', '.a', '.la', '.prl', '.cmake', '.pc',
    '.h', '.hpp', '.hxx', '.c', '.cpp', '.cxx', '.pxd', '.pyx', '.pyi',
    '.m4', '.rst', '.chm', '.md', '.pri', '.bat', '.cmd', '.ps1', '.sh',
})
TOOL_PACKAGES = frozenset({
    'pytest', '_pytest', 'pip', 'setuptools', 'wheel', '_distutils_hack',
    'pkg_resources', 'iniconfig', 'pluggy',
})
RUNTIME_PACKAGES = frozenset({'numpy', 'occ', 'pyside6', 'shiboken6', 'svgwrite',
                              'typing_extensions', 'six'})
APPLICATION_DEVELOPMENT_FILES = frozenset({
    'cadastrophe/diagnostics.py', 'cadastrophe/sample_scene.py',
    'cadastrophe/test_desktop.py', 'cadastrophe/test_runner.py',
})


def normalized(path):
    return PurePosixPath(str(path).replace('\\', '/').lower())


def runtime_file_allowed(path):
    path = normalized(path)
    parts = path.parts
    module = path.name.split('.')[0]
    if any(part in DEVELOPMENT_DIRECTORIES for part in parts[:-1]):
        return False
    notice = path.name.startswith(('license', 'copying', 'notice', 'copyright'))
    if path.suffix in DEVELOPMENT_SUFFIXES and not notice:
        return False
    if path.name.startswith('readme'):
        return False
    if parts[:3] == ('library', 'share', 'info') or parts[:2] == ('share', 'info'):
        return False
    if (path.name.startswith(('test', '_test', 'benchmark'))
            or path.name.split('.')[0].endswith(('_test', '_tests'))
            or path.name == 'conftest.py'):
        return False
    if (module in {'qttest', 'qtquicktest', 'qt6test', 'qt6quicktest', 'icutest', 'icutest78',
                   'boost_unit_test_framework', 'doctest', 'gtester-report', 'gdbus-codegen'}
            or module.startswith(('vtktesting', 'tcltest'))):
        return False
    if parts[:1] in {('scripts',), ('condabin',), ('etc',), ('tools',)}:
        return False
    if parts[:4] in {('library', 'lib', 'qt6', 'metatypes'),
                      ('library', 'lib', 'qt6', 'modules')}:
        return False
    if len(parts) > 3 and parts[:2] == ('library', 'lib') and parts[2].startswith('vtk-') and parts[3] == 'hierarchy':
        return False
    if parts[:1] == ('conda-meta',) and any(path.name.startswith(tool+'-') for tool in TOOL_PACKAGES):
        return False
    if parts[:2] in {('library', 'lib'), ('library', 'mkspecs'),
                     ('library', 'bin')} and path.suffix == '.exe':
        return False
    if parts[:1] == ('libs',) or parts[:2] in {
            ('lib', 'unittest'), ('lib', 'idlelib'), ('lib', 'ensurepip'),
            ('lib', 'turtledemo'), ('lib', '__phello__')}:
        return False
    if parts[:2] == ('lib', 'site-packages') and len(parts) > 2:
        package = parts[2]
        if any(package == tool or package.startswith(tool+'-') for tool in TOOL_PACKAGES):
            return False
        if package in {'pytest.py', 'py.py', 'distutils-precedence.pth'}:
            return False
        if not any(package in {root, root+'.py'} or package.startswith(root+'-') for root in RUNTIME_PACKAGES):
            return False
        if parts[:4] == ('lib', 'site-packages', 'pyside6', 'scripts'):
            return False
    return True


def application_file_allowed(path):
    path = normalized(path)
    if path.name.startswith(('license', 'copying', 'notice', 'copyright', 'attribution', 'provenance')):
        return True
    if path.as_posix() in APPLICATION_DEVELOPMENT_FILES:
        return False
    if any(part in DEVELOPMENT_DIRECTORIES for part in path.parts[:-1]):
        return False
    if path.suffix in {'.pyc', '.pyo', '.log', '.md', '.rst'}:
        return False
    return path.as_posix() != 'plugins/settings.json'
