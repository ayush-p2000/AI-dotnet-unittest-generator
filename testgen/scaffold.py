import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from testgen.dotnet import get_dotnet_cmd
from testgen.logger import get_logger

TFM_RE = re.compile(r"<TargetFramework>\s*([^<\s]+)\s*</TargetFramework>", re.IGNORECASE)
TFMS_RE = re.compile(r"<TargetFrameworks>\s*([^<\s]+)\s*</TargetFrameworks>", re.IGNORECASE)


def run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    logger = get_logger()
    cmd = list(cmd)
    if cmd and cmd[0] == "dotnet":
        cmd[0] = get_dotnet_cmd()
    logger.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.stdout.strip():
        logger.info(result.stdout.strip())
    if result.returncode != 0:
        err_out = (result.stderr.strip() + "\n" + result.stdout.strip()).strip()
        logger.error(err_out)
        err_lines = [
            line.strip() for line in err_out.splitlines()
            if any(k in line for k in [": error ", "Error(s)", "Build FAILED", "error MSB", "error CS", "error CA"])
        ]
        summary = "\n".join(err_lines[:5]) if err_lines else err_out[:300]
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}\n{summary}")
    return result


def detect_target_framework(csproj_path: Path) -> str:
    """Detects target framework, handling <TargetFramework>, <TargetFrameworks>, and Directory.Build.props."""
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

    # Walk up parent directories to check Directory.Build.props
    current = csproj_path.parent
    while current != current.parent:
        props = current / "Directory.Build.props"
        if props.exists():
            props_text = props.read_text(encoding="utf-8-sig", errors="ignore")
            m_props = TFM_RE.search(props_text)
            if m_props:
                return m_props.group(1).strip()
        current = current.parent

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


def ensure_test_project_isolation(test_csproj: Path, tests_dir: Path) -> None:
    """
    Isolates test projects from parent solution's strict code analysis/StyleCop/editorconfig rules.
    1. Creates .editorconfig with root = true in tests_dir to block parent rules like CA1707 (no underscores in method names).
    2. Configures test_csproj to disable analyzers and warnings-as-errors during build.
    """
    logger = get_logger()

    # 1. Isolate via .editorconfig in the test project directory
    editorconfig_path = tests_dir / ".editorconfig"
    if not editorconfig_path.exists():
        editorconfig_content = (
            "root = true\n\n"
            "[*]\n"
            "dotnet_analyzer_diagnostic.severity = none\n\n"
            "[*.cs]\n"
            "dotnet_analyzer_diagnostic.severity = none\n"
            "dotnet_diagnostic.CA1707.severity = none\n"
            "dotnet_diagnostic.SA1600.severity = none\n"
            "dotnet_diagnostic.SA1200.severity = none\n"
            "dotnet_diagnostic.SA1101.severity = none\n"
        )
        editorconfig_path.write_text(editorconfig_content, encoding="utf-8")
        logger.info(f"Created isolated .editorconfig at {editorconfig_path}")

    # 2. Configure test_csproj MSBuild properties
    text = test_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    if "RunAnalyzersDuringBuild" not in text:
        isolation_xml = (
            "\n  <PropertyGroup>\n"
            "    <RunAnalyzersDuringBuild>false</RunAnalyzersDuringBuild>\n"
            "    <EnableNETAnalyzers>false</EnableNETAnalyzers>\n"
            "    <AnalysisMode>None</AnalysisMode>\n"
            "    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>\n"
            "    <CodeAnalysisTreatWarningsAsErrors>false</CodeAnalysisTreatWarningsAsErrors>\n"
            "    <NoWarn>$(NoWarn);NU1605;CA1707;CS1591;SA1600;SA1200;SA1101;SA1633;SA1601</NoWarn>\n"
            "  </PropertyGroup>\n"
        )
        if "</Project>" in text:
            # Clean up older standalone NU1605 block if present
            if "<NoWarn>$(NoWarn);NU1605</NoWarn>" in text:
                text = re.sub(r"\s*<PropertyGroup>\s*<NoWarn>\$\(NoWarn\);NU1605</NoWarn>\s*</PropertyGroup>", "", text)
            new_text = text.replace("</Project>", f"{isolation_xml}</Project>")
            test_csproj.write_text(new_text, encoding="utf-8")
            logger.info(f"Added build isolation properties to {test_csproj.name}")


def ensure_aspnetcore_reference_if_needed(main_csproj: Path, test_csproj: Path) -> None:
    """Ensures test project has Microsoft.AspNetCore.App framework reference when testing web apps or APIs."""
    logger = get_logger()
    main_text = main_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    test_text = test_csproj.read_text(encoding="utf-8-sig", errors="ignore")

    is_web = (
        'Sdk="Microsoft.NET.Sdk.Web"' in main_text
        or "Microsoft.AspNetCore.App" in main_text
        or "Microsoft.AspNetCore" in main_text
    )
    if is_web and "Microsoft.AspNetCore.App" not in test_text:
        framework_ref = (
            "\n  <ItemGroup>\n"
            "    <FrameworkReference Include=\"Microsoft.AspNetCore.App\" />\n"
            "  </ItemGroup>\n"
        )
        if "</Project>" in test_text:
            new_text = test_text.replace("</Project>", f"{framework_ref}</Project>")
            test_csproj.write_text(new_text, encoding="utf-8")
            logger.info(f"Added Microsoft.AspNetCore.App framework reference to {test_csproj.name}")


def ensure_referenced_projects_linked(main_csproj: Path, test_csproj: Path) -> None:
    """Wires sibling ProjectReferences from main project into the test project for multi-project solutions."""
    logger = get_logger()
    main_text = main_csproj.read_text(encoding="utf-8-sig", errors="ignore")
    ref_matches = re.findall(r'<ProjectReference\s+Include="([^"]+)"', main_text, re.IGNORECASE)
    for ref_rel in ref_matches:
        referenced_path = (main_csproj.parent / ref_rel).resolve()
        if referenced_path.exists() and referenced_path != test_csproj:
            test_text = test_csproj.read_text(encoding="utf-8-sig", errors="ignore")
            if referenced_path.name.lower() in test_text.lower():
                continue
            try:
                run(["dotnet", "add", str(test_csproj), "reference", str(referenced_path)])
                logger.info(f"Added sibling project reference to {referenced_path.name}")
            except Exception as e:
                logger.warning(f"Could not link sibling reference {referenced_path.name}: {e}")


def find_solution_file(root_path: Path) -> Optional[Path]:
    """Finds an existing .sln or .slnx file in root_path or up to 3 parent directories."""
    for pattern in ["*.sln", "*.slnx"]:
        matches = list(root_path.glob(pattern))
        if matches:
            return matches[0]

    curr = root_path.parent
    depth = 0
    while curr != curr.parent and depth < 3:
        for pattern in ["*.sln", "*.slnx"]:
            matches = list(curr.glob(pattern))
            if matches:
                return matches[0]
        curr = curr.parent
        depth += 1

    return None


def scaffold_test_project(
    root: str,
    csproj_path: str,
    project_name: str,
    verify_build: bool = True,
) -> Path:
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

    # 2. Suppress package downgrade & analyzer warnings on test project
    ensure_test_project_isolation(test_csproj, tests_dir)

    # 3. Universal references for multi-project solutions & ASP.NET Core
    ensure_aspnetcore_reference_if_needed(main_csproj, test_csproj)
    ensure_referenced_projects_linked(main_csproj, test_csproj)

    # 4. Add core unit testing & mocking packages
    add_package_if_missing(test_csproj, "Moq")
    add_package_if_missing(test_csproj, "FluentAssertions")

    # 5. Add EF Core InMemory provider if main project uses EF Core
    ef_ver = get_efcore_version(main_csproj, tfm)
    if ef_ver:
        add_package_if_missing(test_csproj, "Microsoft.EntityFrameworkCore.InMemory", ef_ver)

    # 6. Wire to solution (safely, never failing scaffolding if sln cannot be updated)
    sln_path = find_solution_file(root_path)
    if not sln_path:
        sln_path = root_path / f"{project_name}.sln"
        try:
            run(["dotnet", "new", "sln", "-n", project_name, "-o", str(root_path), "--force"])
            sln_path = find_solution_file(root_path) or sln_path
            logger.info(f"Created new solution file: {sln_path}")
        except Exception as sln_err:
            logger.warning(f"Could not create solution file: {sln_err}")
            sln_path = None

    if sln_path and sln_path.exists():
        try:
            run(["dotnet", "sln", str(sln_path), "add", str(main_csproj)])
        except Exception:
            pass

        try:
            run(["dotnet", "sln", str(sln_path), "add", str(test_csproj)])
        except Exception:
            pass

    if verify_build:
        # Clean corrupted obj/bin in main project to avoid duplicate attribute errors.
        clean_obj_bin(main_csproj.parent)

        logger.info("\n--- Verifying test project build ---")
        try:
            run(["dotnet", "build", str(test_csproj)])
            logger.info("Test project built successfully!\n")
        except RuntimeError as e:
            # If the test project already had test files and failed due to them,
            # warn instead of hard aborting so the user can use the Critic/Author loop to heal them.
            existing_tests = [f for f in tests_dir.glob("**/*.cs") if f.name != "UnitTest1.cs"]
            if existing_tests:
                logger.warning(f"Build verification warning on existing test files: {e}")
                logger.info("Test project scaffolded; existing test files will be addressed during generation loop.\n")
            else:
                raise

    return test_csproj


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python -m testgen.scaffold <root> <csproj_path> <project_name>")
        sys.exit(1)
    scaffold_test_project(sys.argv[1], sys.argv[2], sys.argv[3])
