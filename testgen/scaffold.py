import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from testgen.logger import get_logger

TFM_RE = re.compile(r"<TargetFramework>\s*([^<\s]+)\s*</TargetFramework>", re.IGNORECASE)
TFMS_RE = re.compile(r"<TargetFrameworks>\s*([^<\s]+)\s*</TargetFrameworks>", re.IGNORECASE)


def run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    logger = get_logger()
    logger.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.stdout.strip():
        logger.info(result.stdout.strip())
    if result.returncode != 0:
        logger.error(result.stderr.strip())
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")
    return result


def detect_target_framework(csproj_path: Path) -> str:
    """Detects target framework, handling both <TargetFramework> and multi-targeting <TargetFrameworks>."""
    text = csproj_path.read_text(encoding="utf-8-sig", errors="ignore")
    m = TFM_RE.search(text)
    if m:
        return m.group(1).strip()

    m_multi = TFMS_RE.search(text)
    if m_multi:
        tfms = [t.strip() for t in m_multi.group(1).split(";") if t.strip()]
        # Prefer net8.0 if present, else highest net* target, else first
        for tfm in ["net8.0", "net9.0", "net7.0", "net6.0"]:
            if tfm in tfms:
                return tfm
        for tfm in tfms:
            if tfm.startswith("net") and "." in tfm:
                return tfm
        return tfms[0] if tfms else "net8.0"

    return "net8.0"


def set_target_framework(csproj_path: Path, tfm: str) -> None:
    # Bug 13 fix: Detect BOM and preserve encoding
    raw_bytes = csproj_path.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if has_bom else "utf-8"

    text = raw_bytes.decode(encoding, errors="ignore")
    if TFM_RE.search(text):
        new_text = TFM_RE.sub(f"<TargetFramework>{tfm}</TargetFramework>", text, count=1)
    elif TFMS_RE.search(text):
        new_text = TFMS_RE.sub(f"<TargetFramework>{tfm}</TargetFramework>", text, count=1)
    else:
        new_text = text
    csproj_path.write_text(new_text, encoding=encoding)


def ensure_internals_visible_to(main_csproj: Path, test_project_name: str) -> None:
    """Grants the test project access to internal classes and methods."""
    logger = get_logger()
    raw_bytes = main_csproj.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if has_bom else "utf-8"

    text = raw_bytes.decode(encoding, errors="ignore")
    if f'InternalsVisibleTo Include="{test_project_name}"' in text or f'InternalsVisibleTo Include="$(AssemblyName).Tests"' in text:
        return

    internals_xml = (
        f"\n  <ItemGroup>\n"
        f"    <InternalsVisibleTo Include=\"{test_project_name}\" />\n"
        f"  </ItemGroup>\n"
    )
    if "</Project>" in text:
        new_text = text.replace("</Project>", f"{internals_xml}</Project>")
        main_csproj.write_text(new_text, encoding=encoding)
        logger.info(f"Granted InternalsVisibleTo to {test_project_name} in {main_csproj.name}")


def ensure_main_project_excludes_tests(main_csproj: Path) -> None:
    """Prevents the main C# project from compiling test files if tests/ is a subfolder."""
    logger = get_logger()
    raw_bytes = main_csproj.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if has_bom else "utf-8"

    text = raw_bytes.decode(encoding, errors="ignore")
    if "tests\\**" in text or "tests/**" in text:
        return

    exclude_xml = (
        "\n  <PropertyGroup>\n"
        "    <DefaultItemExcludes>$(DefaultItemExcludes);tests\\**</DefaultItemExcludes>\n"
        "  </PropertyGroup>\n"
    )
    if "</Project>" in text:
        new_text = text.replace("</Project>", f"{exclude_xml}</Project>")
        main_csproj.write_text(new_text, encoding=encoding)
        logger.info(f"Added tests folder exclusion to {main_csproj.name}")


def add_package_if_missing(test_csproj: Path, package_name: str, version: Optional[str] = None) -> None:
    logger = get_logger()
    text = test_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    if f'Include="{package_name}"' in text or f'include="{package_name}"' in text.lower():
        logger.info(f"Package {package_name} already referenced in {test_csproj.name}")
        return

    cmd = ["dotnet", "add", str(test_csproj), "package", package_name]
    if version:
        cmd.extend(["--version", version])
    try:
        run(cmd)
    except RuntimeError as e:
        logger.warning(f"Warning: Failed to add package {package_name}: {e}")


def get_efcore_version(main_csproj: Path, tfm: str) -> Optional[str]:
    text = main_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    if "Microsoft.EntityFrameworkCore" not in text and "Npgsql.EntityFrameworkCore" not in text:
        return None

    # Check if a specific version is used
    m = re.search(r'Include="Microsoft\.EntityFrameworkCore(?:\.[\w]+)?"\s+Version="([^"]+)"', text)
    if m:
        ver = m.group(1).split(".")[0]  # Get major version like "8" or "9"
        return f"{ver}.*"

    # Fallback to TFM major version (e.g. net8.0 -> 8.*)
    tfm_match = re.search(r"net(\d+)", tfm)
    if tfm_match:
        return f"{tfm_match.group(1)}.*"
    return "8.*"


def clean_obj_bin(folder: Path) -> None:
    """Removes contaminated bin and obj folders if build was corrupted."""
    # Bug 6 fix: searches folder for obj and bin directories properly
    for p in folder.glob("**/obj"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    for p in folder.glob("**/bin"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def ensure_no_package_downgrade_warnings(test_csproj: Path) -> None:
    """Suppresses NU1605 package downgrade warning-as-error when source project has newer package versions."""
    text = test_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    if "NU1605" in text:
        return
    nowarn_xml = (
        "\n  <PropertyGroup>\n"
        "    <NoWarn>$(NoWarn);NU1605</NoWarn>\n"
        "  </PropertyGroup>\n"
    )
    if "</Project>" in text:
        new_text = text.replace("</Project>", f"{nowarn_xml}</Project>")
        test_csproj.write_text(new_text, encoding="utf-8")


def scaffold_test_project(root: str, csproj_path: str, project_name: str) -> Path:
    logger = get_logger()
    root_path = Path(root).resolve()
    main_csproj = Path(csproj_path).resolve()
    tfm = detect_target_framework(main_csproj)

    # 1. Prevent main project from compiling tests subfolder & grant access to internal types
    ensure_main_project_excludes_tests(main_csproj)
    ensure_internals_visible_to(main_csproj, f"{project_name}.Tests")

    tests_dir = root_path / "tests" / f"{project_name}.Tests"
    test_csproj = tests_dir / f"{project_name}.Tests.csproj"

    if not test_csproj.exists():
        tests_dir.parent.mkdir(parents=True, exist_ok=True)
        # Create xUnit test project
        run(["dotnet", "new", "xunit", "-n", f"{project_name}.Tests", "-o", str(tests_dir)])
        set_target_framework(test_csproj, tfm)
        logger.info(f"Set {test_csproj.name} TargetFramework to {tfm}")

        # Wire reference to the target project
        run(["dotnet", "add", str(test_csproj), "reference", str(main_csproj)])

        # Remove boilerplate UnitTest1.cs
        default_test_file = tests_dir / "UnitTest1.cs"
        if default_test_file.exists():
            default_test_file.unlink()
            logger.info(f"Removed boilerplate {default_test_file.name}")
    else:
        logger.info(f"Test project already exists at {test_csproj}")
        default_test_file = tests_dir / "UnitTest1.cs"
        if default_test_file.exists():
            default_test_file.unlink()

    # 2. Suppress package downgrade error NU1605 on test project
    ensure_no_package_downgrade_warnings(test_csproj)

    # 3. Add core unit testing & mocking packages
    add_package_if_missing(test_csproj, "Moq")
    add_package_if_missing(test_csproj, "FluentAssertions")

    # 4. Add EF Core InMemory provider if main project uses EF Core
    ef_ver = get_efcore_version(main_csproj, tfm)
    if ef_ver:
        add_package_if_missing(test_csproj, "Microsoft.EntityFrameworkCore.InMemory", ef_ver)

    # 5. Wire to solution
    sln_candidates = list(root_path.glob("*.sln"))
    if sln_candidates:
        sln_path = sln_candidates[0]
    else:
        sln_path = root_path / f"{project_name}.sln"
        run(["dotnet", "new", "sln", "-n", project_name, "-o", str(root_path)])


    try:
        run(["dotnet", "sln", str(sln_path), "add", str(main_csproj)])
    except RuntimeError:
        pass

    try:
        run(["dotnet", "sln", str(sln_path), "add", str(test_csproj)])
    except RuntimeError:
        pass

    # 5. Clean corrupted obj/bin in main project to avoid duplicate attribute errors (Bug 6 fix)
    clean_obj_bin(main_csproj.parent)

    # 6. Verify that the test project builds successfully
    logger.info("\n--- Verifying test project build ---")
    run(["dotnet", "build", str(test_csproj)])
    logger.info("Test project built successfully!\n")

    return test_csproj


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python -m testgen.scaffold <root> <csproj_path> <project_name>")
        sys.exit(1)
    scaffold_test_project(sys.argv[1], sys.argv[2], sys.argv[3])