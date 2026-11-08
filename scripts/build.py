"""Build the deployment artifacts into dist/.

    python scripts/build.py              # tool zips and the agent zip
    python scripts/build.py --tools-only # fast: no dependency download
    python scripts/build.py --skip-install  # reuse dependencies from the last agent build

Outputs:
    dist/tools/<tool>.zip   one Lambda function each: <tool>.py plus the shared common/ package
    dist/agent.zip          the AgentCore Runtime agent with linux/arm64 dependencies

AgentCore Runtime has several packaging rules, all enforced or checked here:
  * only the arm64 instruction set is supported, and the service rejects any native binary that
    is not arm64 (checked below by reading each ELF header);
  * the zip may be at most 250 MB, 750 MB unzipped;
  * files must be readable: mode 644 for files and 755 for directories. Building on Windows would
    otherwise give meaningless modes, so they are set explicitly.
Zips are written with fixed timestamps so an unchanged build is byte-identical and Terraform
does not see a change.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

TOOLS = ("search_kb", "lookup_customer", "send_email", "issue_refund")

PYTHON_VERSION = "3.13"  # must match runtime = "PYTHON_3_13" in infra/runtime.tf
UV_PLATFORM = "aarch64-manylinux2014"
MAX_ZIP_BYTES = 250 * 1024 * 1024
MAX_UNZIPPED_BYTES = 750 * 1024 * 1024

ELF_MAGIC = b"\x7fELF"
EM_AARCH64 = 183
FIXED_TIME = (2020, 1, 1, 0, 0, 0)

# Native-binary file types that cannot be valid on Linux/arm64 or are never needed.
FOREIGN_BINARY_SUFFIXES = (".pyd", ".dll", ".dylib", ".exe")


# ---------------------------------------------------------------------------------------------
# Zip writing
# ---------------------------------------------------------------------------------------------
def collect(base: Path, prefix: str = "") -> dict[str, Path]:
    """Map archive names to files under ``base``, skipping caches."""
    found: dict[str, Path] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        found[prefix + path.relative_to(base).as_posix()] = path
    return found


def write_zip(target: Path, files: dict[str, Path]) -> None:
    """Write a deterministic zip with explicit permissions (755 directories, 644 files)."""
    target.parent.mkdir(parents=True, exist_ok=True)

    directories: set[str] = set()
    for name in files:
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            directories.add("/".join(parts[:i]) + "/")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for directory in sorted(directories):
            info = zipfile.ZipInfo(directory, FIXED_TIME)
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
        for name in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[name].read_bytes())


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------
def elf_machine(header: bytes) -> int | None:
    """The target architecture of an ELF file, or None if it is not a little-endian ELF."""
    if len(header) < 20 or header[:4] != ELF_MAGIC:
        return None
    return int.from_bytes(header[18:20], "little")


def verify_arm64(zip_path: Path) -> list[str]:
    """Return problems that would make AgentCore Runtime reject this zip."""
    problems: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith(FOREIGN_BINARY_SUFFIXES):
                problems.append(f"foreign binary: {name}")
                continue
            if name.endswith(".so") or ".so." in name:
                with archive.open(info) as handle:
                    machine = elf_machine(handle.read(20))
                if machine is None:
                    problems.append(f"not an ELF file: {name}")
                elif machine != EM_AARCH64:
                    problems.append(f"not arm64 (e_machine={machine}): {name}")
    return problems


def verify_permissions(zip_path: Path) -> list[str]:
    problems = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o777
            expected = 0o755 if info.is_dir() else 0o644
            if mode != expected:
                problems.append(f"{info.filename}: mode {mode:o}, expected {expected:o}")
    return problems


def verify_size(zip_path: Path) -> list[str]:
    problems = []
    if zip_path.stat().st_size > MAX_ZIP_BYTES:
        problems.append(f"zip is {zip_path.stat().st_size / 1e6:.0f} MB; limit is 250 MB")
    with zipfile.ZipFile(zip_path) as archive:
        unzipped = sum(i.file_size for i in archive.infolist())
    if unzipped > MAX_UNZIPPED_BYTES:
        problems.append(f"unzipped size is {unzipped / 1e6:.0f} MB; limit is 750 MB")
    return problems


# ---------------------------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------------------------
def shown(path: Path) -> Path:
    """A path for display: relative to the repo when it is inside it."""
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def build_tools(dist: Path = DIST) -> list[Path]:
    built = []
    common = collect(ROOT / "common", "common/")
    for tool in TOOLS:
        source = ROOT / "tools" / tool / f"{tool}.py"
        files = {f"{tool}.py": source, **common}
        target = dist / "tools" / f"{tool}.zip"
        write_zip(target, files)
        built.append(target)
        print(f"  {shown(target)}  ({target.stat().st_size / 1024:.0f} KB)")
    return built


def install_dependencies(target: Path) -> None:
    """Install the agent's dependencies as linux/arm64 wheels for the runtime's Python."""
    if shutil.which("uv") is None:
        sys.exit(
            "uv is required to fetch arm64 wheels from another OS. "
            "Install it from https://docs.astral.sh/uv/ and rerun."
        )
    if target.exists():
        shutil.rmtree(target)
    command = [
        "uv", "pip", "install",
        "--python-platform", UV_PLATFORM,
        "--python-version", PYTHON_VERSION,
        "--only-binary=:all:",
        "--target", str(target),
        "-r", str(ROOT / "agent" / "requirements.txt"),
    ]
    print("  " + " ".join(command[:8]) + " ...")
    subprocess.run(command, check=True)


def build_agent(dist: Path = DIST, skip_install: bool = False) -> Path:
    staging = dist / ".agent-deps"
    if skip_install and staging.exists():
        print("  reusing dependencies from the last build")
    else:
        install_dependencies(staging)

    # bin/ holds console-script launchers (Windows .exe files when built on Windows). The runtime
    # starts the agent with `python main.py`, so none of them are needed, and a foreign binary
    # would get the whole zip rejected.
    files = {n: p for n, p in collect(staging).items() if not n.startswith("bin/")}
    # The agent code sits at the zip root (main.py is the entry point).
    agent_files = {
        name: path
        for name, path in collect(ROOT / "agent").items()
        if name != "requirements.txt" and not name.startswith("tests/")
    }
    clash = set(files) & set(agent_files)
    if clash:
        raise RuntimeError(f"agent files collide with dependencies: {sorted(clash)[:3]}")
    files.update(agent_files)
    # defenses/ imports only the constants, so ship just those from the shared package.
    files["common/__init__.py"] = ROOT / "common" / "__init__.py"
    files["common/constants.py"] = ROOT / "common" / "constants.py"

    target = dist / "agent.zip"
    write_zip(target, files)

    problems = verify_arm64(target) + verify_permissions(target) + verify_size(target)
    if problems:
        print("\nThe agent zip would be rejected by AgentCore Runtime:")
        for problem in problems[:15]:
            print(f"  - {problem}")
        sys.exit(1)
    print(f"  {shown(target)}  ({target.stat().st_size / 1e6:.1f} MB, arm64 verified)")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tools-only", action="store_true")
    parser.add_argument("--agent-only", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()

    if not args.agent_only:
        print("Building tool functions:")
        build_tools()
    if not args.tools_only:
        print("Building the agent:")
        build_agent(skip_install=args.skip_install)


if __name__ == "__main__":
    main()
