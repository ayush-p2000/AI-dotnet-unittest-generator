# AI-Powered C# Unit Test Generator (.NET 8)

A production-grade, project-agnostic autonomous unit test generation toolchain for any .NET 8 C# project. Uses a **2-Agent Loop** (Author Agent + Critic Agent) powered by the **Google Gemini API** to generate high-coverage unit tests with **xUnit**, **Moq**, and **FluentAssertions**.

---

## Table of Contents

1. [Prerequisites](#-prerequisites)
2. [Installation & Setup](#-installation--setup)
3. [Quick Start (TL;DR)](#-quick-start-tldr)
4. [Step-by-Step Usage](#-step-by-step-usage)
   - [Step 1: Scan the Target Project](#step-1-scan-the-target-project)
   - [Step 2: Scaffold the Test Project](#step-2-scaffold-the-test-project)
   - [Step 3: Verify Setup (Optional)](#step-3-verify-setup-optional)
   - [Step 4a: Generate Tests for a Single File](#step-4a-generate-tests-for-a-single-file)
   - [Step 4b: Batch Generate for Entire Project](#step-4b-batch-generate-for-entire-project)
   - [Step 5: Run Tests Manually](#step-5-run-tests-manually)
5. [Checkpointing & Resume](#-checkpointing--resume)
6. [CLI Reference](#-cli-reference)
7. [How It Works (Architecture)](#-how-it-works-architecture)
8. [Project Structure](#-project-structure)
9. [Folder Mirroring](#-folder-mirroring)
10. [Multi-Key API Rotation](#-multi-key-api-rotation)
11. [What Gets Skipped](#-what-gets-skipped-automatically)
12. [Troubleshooting](#-troubleshooting)

---

## 📋 Prerequisites

Before you start, make sure you have:

| Requirement | How to check | Install link |
|---|---|---|
| **.NET 8 SDK** | `dotnet --version` (>= 8.0) | [dotnet.microsoft.com](https://dotnet.microsoft.com/download/dotnet/8.0) |
| **Python 3.14+** | `python --version` (>= 3.14) | [python.org](https://www.python.org/downloads/) |
| **Gemini API Key** | N/A | [Google AI Studio](https://aistudio.google.com/apikey) |

---

## 🛠️ Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/nemotron-csharp-testgen.git
cd nemotron-csharp-testgen
```

### 2. Create & Activate Python Virtual Environment

```bash
# Create venv
python -m venv .venv

# Activate (pick your shell):
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Windows CMD:
.\.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate
```

### 3. Install Python Dependencies

```bash
pip install openai python-dotenv
```

### 4. Configure API Keys

Create a `.env` file in the project root:

```env
# Required: Primary Gemini API key
GEMINI_API_KEY=your_primary_api_key_here

# Optional: Additional keys for automatic failover when rate-limited
GEMINI_API_KEY_2=your_second_key_here
GEMINI_API_KEY_3=your_third_key_here
# You can add as many as you want: GEMINI_API_KEY_4, GEMINI_API_KEY_5, etc.
```

> **Tip:** Free-tier Gemini keys have rolling per-minute and per-day quotas. Adding 2-3 keys lets the tool automatically rotate when one key is rate-limited, significantly increasing throughput before hitting quota limits.

---

## ⚡ Quick Start (TL;DR)

If you just want to get going, run these 4 commands in order:

```bash
# 1. Scan your .NET project
python scan_project.py "C:\path\to\your\dotnet-project"

# 2. Scaffold xUnit test project
python scaffold_tests.py

# 3. Generate tests for a single file
python generate_tests.py MyService.cs

# 4. OR batch-generate for the entire project
python batch_generate.py
```

That's it. Read below for the full details.

---

## 📖 Step-by-Step Usage

### Step 1: Scan the Target Project

The scanner walks through the target .NET project, parses all `.cs` files using AST analysis, and produces a **manifest** (`scan_output.json`) containing:
- All projects and their `.csproj` paths
- Every C# file with its types (classes, interfaces, enums, records)
- Methods, constructors, properties, and parameter signatures
- Dependencies and inheritance chains

```bash
python scan_project.py "C:\path\to\your\dotnet-project"
```

**Example:**
```
> python scan_project.py "C:\Projects\MyWebApi"

Found 1 project(s), 91 file(s), 114 type(s).
  - MyWebApi: 91 file(s)
Written to scan_output.json
```

**For multi-project solutions** (e.g. Elsa Workflows), just point it at the solution root:
```bash
python scan_project.py "C:\Projects\elsa-workflows"
# Output: Found 12 project(s), 847 file(s), 1203 type(s).
```

**Output file:** `scan_output.json` (saved in the current directory)

---

### Step 2: Scaffold the Test Project

Reads `scan_output.json` and creates a fully configured xUnit test project for each source project:

```bash
python scaffold_tests.py
```

**What this does:**
1. Creates `tests/<ProjectName>.Tests/` directory
2. Runs `dotnet new xunit` with the correct `<TargetFramework>` (auto-detected from your `.csproj`)
3. Installs NuGet packages: `Moq`, `FluentAssertions`, `coverlet.collector`
4. If your project uses EF Core, installs `Microsoft.EntityFrameworkCore.InMemory`
5. Adds `<ProjectReference>` to the source project
6. Injects `<InternalsVisibleTo>` into the source `.csproj` so `internal` classes can be tested
7. Adds `tests/` folder exclusion to the source `.csproj` (prevents test files from compiling with the main project)
8. Runs `dotnet build` to verify everything compiles

**Example output:**
```
Added tests folder exclusion to MyWebApi.csproj
Granted InternalsVisibleTo to MyWebApi.Tests in MyWebApi.csproj
Added package Moq to MyWebApi.Tests.csproj
Added package FluentAssertions to MyWebApi.Tests.csproj
Test project built successfully!
Test project ready: C:\Projects\MyWebApi\tests\MyWebApi.Tests\MyWebApi.Tests.csproj
```

> **Note:** This step is idempotent -- running it again won't duplicate packages or references.

---

### Step 3: Verify Setup (Optional)

Confirm your Gemini API key works:

```bash
python verify_setup.py
```

**Expected output:**
```
GEMINI_OK
```

If this fails, double-check your `.env` file has a valid `GEMINI_API_KEY`.

---

### Step 4a: Generate Tests for a Single File

Target one specific `.cs` file for test generation:

```bash
python generate_tests.py <FileName.cs>
```

**Examples:**
```bash
# Basic -- uses default settings (90% coverage, 4 retries, gemini-3.6-flash)
python generate_tests.py AuthService.cs

# Custom coverage target
python generate_tests.py BookingsController.cs --coverage 95

# More retries for complex files
python generate_tests.py CarsService.cs --retries 6

# Use a different model
python generate_tests.py ReviewsService.cs --model gemini-2.5-pro

# Multi-project solution -- specify which project
python generate_tests.py AuthService.cs --project Elsa.Workflows.Core
```

**What happens behind the scenes:**
1. **Context Assembly** -- Reads the source file plus all its dependencies (interfaces, models, entities) from the manifest
2. **Folder Detection** -- Determines the subfolder path (e.g. `Services/`, `Controllers/Payments/`)
3. **Existing Test Check** -- If `<Name>Tests.cs` already exists, evaluates it first. If it already has >= 90% coverage, skips immediately
4. **Author Agent** -- Sends source code + context to Gemini to generate comprehensive xUnit tests
5. **Critic Agent** -- Compiles and runs the tests with `dotnet test --collect:"XPlat Code Coverage"`, parses Cobertura XML for line coverage, and extracts any compiler errors or assertion failures
6. **Iterative Refinement** -- If coverage < 90% or tests fail, feeds the exact error messages and uncovered line numbers back to the Author Agent. Repeats up to `--retries` times
7. **Result** -- Final test file saved with exact folder mirroring

---

### Step 4b: Batch Generate for Entire Project

Process **all testable files** in the project automatically:

```bash
python batch_generate.py
```

**Advanced usage:**
```bash
# Process all projects in a multi-project solution
python batch_generate.py --all-projects

# Target a specific project
python batch_generate.py --project MyWebApi

# Process a single file through the batch system (uses checkpointing)
python batch_generate.py --only-file BookingsService.cs

# Custom settings
python batch_generate.py --coverage 95 --retries 6 --model gemini-2.5-pro

# Force re-evaluation of all files (ignore checkpoint)
python batch_generate.py --force

# Skip the auto-skip logic and process everything
python batch_generate.py --no-skip
```

**What happens:**
1. Loads the manifest and identifies all source files
2. **Auto-skips** files with no testable logic (DTOs, enums, interfaces, migrations, etc.)
3. **Checks checkpoint** (`.testgen_state.json`) -- instantly skips files already at >= 90% coverage
4. For each remaining file, runs the full Author -> Critic -> Refine loop
5. Saves progress atomically after every file to the checkpoint
6. If all API keys hit rate limits, exits gracefully with progress saved
7. Prints a summary report and saves it to `batch_report.json`

---

### Step 5: Run Tests Manually

After generation, run your tests anytime using the standard .NET CLI:

```bash
# Run all tests
dotnet test "C:\path\to\tests\Project.Tests\Project.Tests.csproj"

# Run with code coverage report
dotnet test "C:\path\to\tests\Project.Tests\Project.Tests.csproj" --collect:"XPlat Code Coverage"

# Run a specific test class
dotnet test --filter "FullyQualifiedName~AuthServiceTests"

# Verbose output
dotnet test -v detailed
```

The Cobertura coverage XML will be in `tests/Project.Tests/TestResults/*/coverage.cobertura.xml`.

---

## 🔄 Checkpointing & Resume

The system uses **`.testgen_state.json`** to track progress. This file is updated atomically after every file completes.

### How it works:

| Scenario | What happens |
|---|---|
| **Ctrl+C / Crash / Power loss** | All completed files are saved. Restart with `python batch_generate.py` to resume. |
| **API rate limit on all keys** | Progress saved, script exits cleanly with code 0. Run again later. |
| **Normal re-run** | Files with >= 90% coverage are instantly skipped (0 API calls, 0 build time). |
| **Force fresh start** | Use `--force` to ignore the checkpoint and re-evaluate everything. |
| **Different state file** | Use `--state-file custom.json` for a separate checkpoint. |

### State file structure (`.testgen_state.json`):
```json
{
  "ProjectName": {
    "AuthService.cs": {
      "status": "SUCCESS",
      "coverage_pct": 100.0,
      "iterations": 2,
      "test_file": "C:\\...\\tests\\Project.Tests\\Services\\AuthServiceTests.cs",
      "last_updated": "2026-09-01T12:00:43Z"
    }
  }
}
```

---

## 📚 CLI Reference

### `scan_project.py`
```
python scan_project.py <path-to-project-root>
```
| Argument | Required | Description |
|---|---|---|
| `path` | Yes | Absolute or relative path to the .NET project/solution root |

**Output:** `scan_output.json`

---

### `scaffold_tests.py`
```
python scaffold_tests.py
```
No arguments. Reads `scan_output.json` from the current directory.

---

### `generate_tests.py`
```
python generate_tests.py <FileName.cs> [options]
```
| Argument / Flag | Default | Description |
|---|---|---|
| `target_file` | *(required)* | Name of the C# file to generate tests for |
| `--manifest` | `scan_output.json` | Path to the scan manifest JSON |
| `--project` | *(auto-detect)* | Project name (required for multi-project solutions) |
| `--model` | `gemini-3.6-flash` | Gemini model name |
| `--coverage` | `90.0` | Target code coverage percentage |
| `--retries` | `4` | Max Author->Critic iterations |

---

### `batch_generate.py`
```
python batch_generate.py [options]
```
| Flag | Default | Description |
|---|---|---|
| `--manifest` | `scan_output.json` | Path to the scan manifest JSON |
| `--project` | *(auto-detect)* | Target a specific project by name |
| `--all-projects` | `false` | Process all projects in the solution sequentially |
| `--model` | `gemini-3.6-flash` | Gemini model name |
| `--coverage` | `90.0` | Target code coverage percentage |
| `--retries` | `4` | Max Author->Critic iterations per file |
| `--concurrency` | `1` | Number of parallel workers |
| `--only-file` | *(none)* | Process only a specific file |
| `--resume` | `false` | Skip files with existing passing tests |
| `--force` | `false` | Ignore saved checkpoint, re-evaluate all files |
| `--state-file` | `.testgen_state.json` | Path to checkpoint file |
| `--report` | `batch_report.json` | Output report JSON path |
| `--no-skip` | `false` | Don't auto-skip any files |

---

### `verify_setup.py`
```
python verify_setup.py
```
No arguments. Checks that the Gemini API key in `.env` is valid.

---

## 🏗️ How It Works (Architecture)

```
+-------------------------------------------------------------------------+
|                        batch_generate.py                                |
|   Orchestrates all files, manages checkpointing, handles rate limits    |
+---------------------------------+---------------------------------------+
                                  |
                      +-----------v-----------+
                      |   testgen/agent_loop   |  <-- Per-file loop controller
                      +-----------+-----------+
                                  |
                +-----------------+------------------+
                v                                    v
     +-------------------+                +-----------------------+
     |  testgen/author    |                |   testgen/critic      |
     |  (Gemini LLM)      | ---- loop --> |   (dotnet test)       |
     |                     | <-- feedback--|   (Cobertura XML)     |
     |  Generates test     |               |   Evaluates tests     |
     |  code via AI        |               |   & coverage          |
     +-------------------+                +-----------------------+
                ^
                |
     +-------------------+
     |  testgen/context    |  <-- Builds full dependency context (models,
     |                     |      entities, interfaces, base classes) from
     +-------------------+      the AST manifest

     Supporting modules:
     +------------------+  +-------------------+  +------------------+
     | testgen/scanner   |  | testgen/scaffold   |  | testgen/state    |
     | AST parser        |  | Test project       |  | Checkpoint       |
     | (regex-based)     |  | scaffolder         |  | persistence      |
     +------------------+  +-------------------+  +------------------+
```

### The Author -> Critic Loop:

1. **Author Agent** receives: source file code, all dependency code, project namespace, existing test code (if any), and gap analysis
2. **Author** generates a complete xUnit test file via Gemini API
3. **Critic Agent** compiles the test project and runs `dotnet test --collect:"XPlat Code Coverage"`
4. **Critic** parses: compiler errors (CS0117, CS9035, etc.), test failures with stack traces, and line-by-line coverage from Cobertura XML
5. If **coverage >= target** and **all tests pass** -> **SUCCESS**, save and move on
6. If not -> feed the exact errors and uncovered lines back to the Author. Repeat up to `--retries` times

---

## 📁 Project Structure

For the extensible package layout and guidance on where to add features, see
[FOLDER_STRUCTURE.md](FOLDER_STRUCTURE.md).

```
nemotron-csharp-testgen/
|-- .env                      # API keys (git-ignored)
|-- .testgen_state.json       # Checkpoint file (auto-generated)
|-- scan_output.json          # AST manifest (auto-generated by Step 1)
|-- batch_report.json         # Batch run report (auto-generated)
|
|-- scan_project.py           # Step 1: Scan target project
|-- scaffold_tests.py         # Step 2: Scaffold xUnit test project
|-- verify_setup.py           # Step 3: Verify Gemini API key
|-- generate_tests.py         # Step 4a: Single-file test generation
|-- batch_generate.py         # Step 4b: Batch test generation
|
|-- testgen/                  # Extensible application package
|   |-- cli/                  # Command implementations
|   |-- agents/               # Gemini author and dotnet coverage critic
|   |-- application/          # Loop orchestration, context, checkpoint state
|   |-- project/              # C# scanning and test-project scaffolding
|   |-- infrastructure/       # Shared services (logging today)
|   `-- <legacy shims>        # Existing testgen.* imports remain supported
|
|-- walkthrough.md            # Detailed technical walkthrough
|-- README.md                 # This file
```

---

## 📂 Folder Mirroring

Generated tests **exactly mirror** the folder structure of the source project and end with `Test.cs`:

```
Source Project                              Test Project
--------------                              ------------
MyProject/                                  tests/MyProject.Tests/
|-- Controllers/                            |-- Controllers/
|   |-- AuthController.cs        -->        |   |-- AuthControllerTest.cs
|   |-- Payments/                           |   |-- Payments/
|       |-- PaymentController.cs -->        |       |-- PaymentControllerTest.cs
|-- Services/                               |-- Services/
|   |-- AuthService.cs           -->        |   |-- AuthServiceTest.cs
|   |-- BookingsService.cs       -->        |   |-- BookingsServiceTest.cs
|-- AppConfig/                              |-- AppConfig/
|   |-- AutofacModule.cs        -->         |   |-- AutofacModuleTest.cs
|-- Utils/                                  |-- Utils/
    |-- Location/                               |-- Location/
        |-- DistanceCalc.cs      -->                |-- DistanceCalcTest.cs
```


---

## 🔑 Multi-Key API Rotation

The tool automatically detects all Gemini API keys in your `.env` file matching the pattern `GEMINI_API_KEY*`:

```env
GEMINI_API_KEY=key1          # Primary
GEMINI_API_KEY_2=key2        # Fallback 1
GEMINI_API_KEY_3=key3        # Fallback 2
GEMINI_API_KEY_EXTRA=key4    # Also detected!
```

**Rotation behavior:**
1. Starts with the first key
2. On `429 RESOURCE_EXHAUSTED` (rate limit), automatically switches to the next key
3. Logs: `[KEY ROTATION] Switched to Gemini API Key #2`
4. If **all keys** are exhausted -> saves progress to checkpoint and exits cleanly
5. On next run, picks up exactly where it stopped

---

## 🚫 What Gets Skipped Automatically

The batch generator intelligently skips files that don't contain testable logic:

| Category | Examples | Why skipped |
|---|---|---|
| **DTOs / Models** | `BookingDto.cs`, `User.cs` | No methods, pure data containers |
| **Enums** | `FuelType.cs`, `UserType.cs` | No logic to test |
| **Interfaces** | `IUserService.cs` | No implementation |
| **EF Migrations** | `20250601_InitialCreate.cs` | Auto-generated, in `Migrations/` folder |
| **DbContext** | `AppDbContext.cs` | Configuration, not business logic |
| **Entry Points** | `Program.cs`, `Startup.cs` | Boilerplate |
| **Designer Files** | `*.Designer.cs`, `*.g.cs` | Auto-generated |

Use `--no-skip` to override this and process everything.

---

## 🔧 Troubleshooting

### "Manifest not found" error
```
[ERROR] Manifest not found at 'scan_output.json'. Run scan_project.py first.
```
**Fix:** Run `python scan_project.py "C:\path\to\your\project"` first.

---

### "Test project not found" error
```
[WARNING] Test project not found at tests/MyProject.Tests/MyProject.Tests.csproj
```
**Fix:** Run `python scaffold_tests.py` first.

---

### "GEMINI_API_KEY not found" error
**Fix:** Create a `.env` file in the project root with `GEMINI_API_KEY=your_key_here`.

---

### Rate limit / quota exhaustion
```
[ALL KEYS EXHAUSTED] All provided Gemini API keys have reached their quota limits.
```
**This is normal on the free tier.** Your progress is saved. Just run `python batch_generate.py` again later (the free-tier quota resets every minute for RPM and every day for RPD).

---

### Compiler errors in generated tests
The Critic Agent catches these automatically and feeds them back to the Author Agent for correction. If you're seeing persistent compiler errors after max retries, the file may need complex mocking patterns that the model struggles with. Try:
```bash
python generate_tests.py ProblemFile.cs --retries 6 --model gemini-2.5-pro
```

---

### Windows encoding errors (Unicode characters in terminal)
All terminal output uses ASCII-only characters. If you still see encoding issues, set:
```powershell
$env:PYTHONIOENCODING = "utf-8"
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
