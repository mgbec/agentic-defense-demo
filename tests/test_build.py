import stat
import struct
import zipfile
from pathlib import Path

import scripts.build as build


def make_elf(machine: int) -> bytes:
    """The first 20 bytes of a little-endian 64-bit ELF header for the given architecture."""
    header = bytearray(20)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little-endian
    struct.pack_into("<H", header, 18, machine)
    return bytes(header)


def write(tmp_path: Path, files: dict[str, bytes]) -> Path:
    sources = {}
    for name, data in files.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sources[name] = path
    target = tmp_path / "out.zip"
    build.write_zip(target, sources)
    return target


# ---------------------------------------------------------------- ELF checks
def test_elf_machine_reads_the_architecture():
    assert build.elf_machine(make_elf(build.EM_AARCH64)) == 183
    assert build.elf_machine(make_elf(62)) == 62  # x86-64


def test_elf_machine_rejects_non_elf_data():
    assert build.elf_machine(b"MZ\x90\x00" + b"\x00" * 16) is None
    assert build.elf_machine(b"short") is None


def test_an_arm64_library_passes(tmp_path):
    zip_path = write(tmp_path, {"lib/_speed.so": make_elf(183), "app.py": b"x"})
    assert build.verify_arm64(zip_path) == []


def test_an_x86_library_is_rejected(tmp_path):
    zip_path = write(tmp_path, {"lib/_speed.so": make_elf(62)})
    problems = build.verify_arm64(zip_path)
    assert len(problems) == 1 and "not arm64" in problems[0]


def test_versioned_shared_objects_are_checked_too(tmp_path):
    zip_path = write(tmp_path, {"lib/libfoo.so.1": make_elf(62)})
    assert build.verify_arm64(zip_path)


def test_windows_and_mac_binaries_are_rejected(tmp_path):
    zip_path = write(tmp_path, {"bin/tool.exe": b"MZ", "x.dll": b"MZ", "y.dylib": b"\xcf\xfa"})
    assert len(build.verify_arm64(zip_path)) == 3


def test_a_shared_object_that_is_not_elf_is_rejected(tmp_path):
    assert build.verify_arm64(write(tmp_path, {"fake.so": b"not a library at all"}))


# ---------------------------------------------------------------- zip properties
def test_files_are_644_and_directories_are_755(tmp_path):
    zip_path = write(tmp_path, {"a/b/c.py": b"x", "top.py": b"y"})
    assert build.verify_permissions(zip_path) == []
    with zipfile.ZipFile(zip_path) as archive:
        modes = {i.filename: (i.external_attr >> 16) for i in archive.infolist()}
    assert stat.S_IMODE(modes["a/"]) == 0o755 and stat.S_ISDIR(modes["a/b/"])
    assert stat.S_IMODE(modes["top.py"]) == 0o644


def test_parent_directories_get_explicit_entries(tmp_path):
    zip_path = write(tmp_path, {"a/b/c.py": b"x"})
    with zipfile.ZipFile(zip_path) as archive:
        assert {"a/", "a/b/", "a/b/c.py"} <= set(archive.namelist())


def test_wrong_permissions_are_reported(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        info = zipfile.ZipInfo("x.py")
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(info, b"x")
    assert build.verify_permissions(bad)


def test_builds_are_byte_identical(tmp_path):
    files = {"a.py": b"print(1)", "b/c.py": b"print(2)"}
    first = write(tmp_path / "one", files).read_bytes()
    second = write(tmp_path / "two", files).read_bytes()
    assert first == second


def test_size_limit_is_enforced(tmp_path, monkeypatch):
    zip_path = write(tmp_path, {"a.py": b"x" * 5000})
    monkeypatch.setattr(build, "MAX_UNZIPPED_BYTES", 100)
    assert build.verify_size(zip_path)


# ---------------------------------------------------------------- the real tool builds
def test_each_tool_zip_has_its_handler_and_the_shared_package(tmp_path):
    built = build.build_tools(tmp_path)
    assert len(built) == len(build.TOOLS)
    for zip_path in built:
        tool = zip_path.stem
        names = set(zipfile.ZipFile(zip_path).namelist())
        assert f"{tool}.py" in names
        assert {"common/__init__.py", "common/constants.py", "common/sink.py"} <= names
        assert build.verify_permissions(zip_path) == []


def test_a_tool_zip_is_importable_and_runs(tmp_path, monkeypatch):
    """Unzip a real tool build into a clean directory and call its handler from there."""
    import importlib
    import sys

    build.build_tools(tmp_path)
    target = tmp_path / "unzipped"
    zipfile.ZipFile(tmp_path / "tools" / "lookup_customer.zip").extractall(target)

    for name in [m for m in sys.modules if m == "common" or m.startswith("common.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, "lookup_customer", raising=False)
    monkeypatch.syspath_prepend(str(target))

    module = importlib.import_module("lookup_customer")
    assert Path(module.__file__).is_relative_to(target)
    assert module.handler({"customer_id": "C-1002"})["customer"]["name"] == "Priya Nair"
    del sys.modules["lookup_customer"]
    for name in [m for m in sys.modules if m == "common" or m.startswith("common.")]:
        del sys.modules[name]


def test_module_constants_are_sane():
    assert build.MAX_ZIP_BYTES == 250 * 1024 * 1024
    assert build.UV_PLATFORM.startswith("aarch64")
