# AI-Powered C# Unit Test Generator — Step 5 Final Walkthrough

A fully automated, project-agnostic 2-agent (Author + Critic) unit test generator for .NET 8 solutions using Google Gemini, xUnit, Moq, and FluentAssertions.

---

## 1. Key Architectural Components

### 1.1 Project Scanner (`testgen/scanner.py` & `scan_project.py`)
- Traverses any .NET solution or project root.
- Extracts C# AST metadata: namespaces, usings, classes, records, structs, interfaces, enums, constructors (including primary constructors), properties, methods, constants.
- Generates a **Global Symbol Table** mapping all types across project boundaries.
- Produces `scan_output.json`.

### 1.2 Test Project Scaffolder (`testgen/scaffold.py` & `scaffold_tests.py`)
- Automatically detects the target framework (`net8.0`).
- Generates an `xUnit` test project referencing the main project and solution.
- Adds required NuGet packages: `xunit`, `Moq`, `FluentAssertions`, `Microsoft.EntityFrameworkCore.InMemory`.
- Excludes test subdirectories from the main project build.

### 1.4 Directory Nesting & Mirroring (`testgen/agent_loop.py` & `testgen/context.py`)
- Automatically detects the relative folder path of any source file relative to its project root (e.g. `Controllers/`, `Services/`, `Common/Attributes/`, `utils/location/`).
- Creates the exact identical folder hierarchy inside the test project (e.g. `tests/TicDrive.Tests/Controllers/AuthControllerTests.cs`, `tests/TicDrive.Tests/Services/AuthServiceTests.cs`).
- Correlates test namespaces and files with 1:1 parity with the source code structure.

- Recursively resolves dependencies (DTOs, injected interfaces, DbSets, entity models, enums).
- Extracts full source code snippets for all dependent entities so the Author Agent sees required properties, enum validations, and navigation properties.

### 1.4 Dual-Agent Loop (`testgen/author.py`, `testgen/critic.py`, `testgen/agent_loop.py`)
- **Author Agent (Gemini)**: Writes complete xUnit test files following best practices (isolated in-memory DBs, boundary conditions, edge cases, null arguments).
- **Critic Agent (Runner)**: Compiles the test project via `dotnet test --collect:"XPlat Code Coverage"`, parses compiler errors, test failure stack traces, and line-by-line Cobertura coverage.
- **Auto-Healing Loop**: Feeds back exact error lines and uncovered line numbers to the Author Agent to refine the test code iteratively up to the target threshold (>= 90%).

### 1.5 State Tracker & Progress Checkpointing (`testgen/state.py` & `.testgen_state.json`)
- **Atomic State Persistence**: Tracks every source file, its test path, iteration count, coverage %, and completion status in `.testgen_state.json`.
- **Instant Resume**: Upon starting or resuming, reads `.testgen_state.json` to skip already-completed files instantly without wasting CPU time or API tokens.
- **Fail-Safe & Interruption-Proof**: Updates the checkpoint after every single file. If the process is stopped (Ctrl+C, power outage, or API rate limit), all progress is safely saved and immediately resumable with `python batch_generate.py`.
- **Force Overwrite (`--force`)**: Allows re-evaluating all files from scratch when requested.

### 1.6 Centralized Logging System (`testgen/logger.py`)
- **Dedicated Destination**: Automatically creates and writes execution logs to `Documents/Logs/<session>_<YYYYMMDD_HHMMSS>.log` on any Windows device (`C:\Users\<Username>\Documents\Logs`).
- **Comprehensive Traceability**: Logs file processing steps, lines covered vs total lines, exact uncovered line lists, test runner outputs, compiler errors, API rotations, and rate limit states.
- **Dual-Stream**: Outputs clean feedback to console while logging structured timestamps and severity levels to disk.


---

## 2. Usage Guide

### Step 1: Scan Target Project
```bash
python scan_project.py <path-to-dotnet-project>
```

### Step 2: Scaffold Test Project
```bash
python scaffold_tests.py
```

### Step 3: Run Single File Generation / Refinement
```bash
python generate_tests.py ReviewsService.cs
```

### Step 4: Run Solution-Wide Batch Orchestrator
```bash
python batch_generate.py --manifest scan_output.json --coverage 90
```

---

## 3. Verified Coverage Results

| Component / File | Generated Tests | Final Coverage | Auto-Healed |
|---|---|---|---|
| `DistanceCalculator.cs` | `DistanceCalculatorTests.cs` | **100.0%** | Iteration 1 |
| `ImagesUtils.cs` | `ImagesUtilsTests.cs` | **100.0%** | Iteration 1 |
| `ReviewsService.cs` | `ReviewsServiceTests.cs` | **100.0%** | Iteration 1 |
| `UserClaimsMapper.cs` | `UserClaimsMapperTests.cs` | **100.0%** | Iteration 1 |
| `RequiredIfUserTypeAttribute.cs` | `RequiredIfUserTypeAttributeTests.cs` | **100.0%** | Iteration 1 |
| `AuthController.cs` | `AuthControllerTests.cs` | **99.8%** | Iteration 1 |
| `BookingsController.cs` | `BookingsControllerTests.cs` | **100.0%** | Iteration 1 |
| `CarsController.cs` | `CarsControllerTests.cs` | **100.0%** | Iteration 1 |
| `CustomerController.cs` | `CustomerControllerTests.cs` | **100.0%** | Iteration 2 (Fixed pagination) |
| `DateTimeController.cs` | `DateTimeControllerTests.cs` | **100.0%** | Iteration 1 |
| `ImagesController.cs` | `ImagesControllerTests.cs` | **100.0%** | Iteration 2 (Fixed DTO properties) |
| `LanguagesController.cs` | `LanguagesControllerTests.cs` | **100.0%** | Iteration 1 |
| `LegalDeclarationsController.cs` | `LegalDeclarationsControllerTests.cs` | **100.0%** | Iteration 1 |
| `OfferedServicesController.cs` | `OfferedServicesControllerTests.cs` | **100.0%** | Iteration 1 |
| `ReviewsController.cs` | `ReviewsControllerTests.cs` | **100.0%** | Iteration 1 |
| `ServicesController.cs` | `ServicesControllerTests.cs` | **100.0%** | Iteration 1 |
| `StatisticsController.cs` | `StatisticsControllerTests.cs` | **100.0%** | Iteration 2 (Fixed Enum) |
| `WorkshopsController.cs` | `WorkshopsControllerTests.cs` | **100.0%** | Iteration 1 |
| `AuthService.cs` | `AuthServiceTests.cs` | **100.0%** | Iteration 2 (Fixed required DTO) |
