# AI-Powered C# Unit Test Generator & Refactoring Studio (.NET 8)

A production-grade, project-agnostic autonomous unit test generation and code-refactoring platform for any .NET 8 C# project. Powered by a **2-Agent Loop** (Author Agent + Critic Agent) with multi-agent support for **Local Ollama (Qwen 3 Coder)** and **Google Gemini** to generate high-coverage unit tests with **xUnit**, **Moq**, and **FluentAssertions**, alongside an integrated **Web UI Dashboard** and **SonarQube Cognitive Complexity Refactoring Agent**.

---

## Table of Contents

1. [Key Capabilities](#-key-capabilities)
2. [Prerequisites](#-prerequisites)
3. [Installation & Setup](#-installation--setup)
4. [Quick Start (Web Dashboard or CLI)](#-quick-start)
   - [Option A: Modern Web Dashboard (Recommended)](#option-a-modern-web-dashboard-recommended)
   - [Option B: Command Line Interface (CLI)](#option-b-command-line-interface-cli)
5. [Autonomous Test Generation Workflow](#-autonomous-test-generation-workflow)
6. [Multi-Agent Selection: Qwen 3 Coder vs Gemini](#-multi-agent-selection)
7. [SonarQube Cognitive Complexity & Auto-Refactoring](#-sonarqube-cognitive-complexity--auto-refactoring)
8. [CLI Reference](#-cli-reference)
9. [Architecture & How It Works](#-architecture--how-it-works)
10. [Troubleshooting](#-troubleshooting)

---

## 🚀 Key Capabilities

- **Multi-Agent Provider Choice**: Switch seamlessly between **Local Ollama (Qwen 3 Coder)** for zero-cost, private offline generation and **Google Gemini** (`gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-2.5-pro`) with automated multi-key quota failover.
- **100% Autonomous Batch Execution**: Point at any C# project or multi-project solution. It auto-scaffolds the test project, resolves dependencies, and generates, builds, tests, and refines unit tests across all testable files without manual intervention.
- **Web UI Dashboard**: Modern, dark-mode browser interface (`http://localhost:5000`) with agent dropdown selector, dynamic model discovery, project selector, real-time log streaming via Server-Sent Events (SSE), and SonarQube refactoring workbench.
- **Full, Untruncated Stack Traces (TRX Integration)**: Integrates directly with Visual Studio Test Results (`.trx`) XML to feed the complete, untruncated runtime stack traces and compiler error codes back to the Author Agent for accurate self-correction.
- **ASP.NET Core & Universal .NET 8 Support**: Automatic detection of `Microsoft.NET.Sdk.Web` / ASP.NET Core, automatically configuring `<FrameworkReference Include="Microsoft.AspNetCore.App" />`, `DefaultHttpContext`, `TempDataDictionary`, and `Moq`/`FluentAssertions` setup.
- **SonarQube Complexity Refactoring**: Analyzes functions exceeding cognitive complexity thresholds, generates refactored clean code, validates build integrity, and creates automatic backups before applying changes.

---

## 📋 Prerequisites

| Requirement | Verification | Link |
|---|---|---|
| **.NET 8 SDK** | `dotnet --version` (>= 8.0) | [dotnet.microsoft.com](https://dotnet.microsoft.com/download/dotnet/8.0) |
| **Python 3.10+** | `python --version` (>= 3.10) | [python.org](https://www.python.org/downloads/) |
| **Ollama (Qwen 3 Coder)** | `ollama run qwen3-coder:latest` | [ollama.com](https://ollama.com/) |
| **Gemini API Key (Optional)** | Defined in `.env` | [Google AI Studio](https://aistudio.google.com/apikey) |
| **SonarQube / SonarCloud (Optional)** | URL & Token | [sonarqube.org](https://www.sonarqube.org/) |

---

## 🛠️ Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/ayush-p2000/AI-dotnet-unittest-generator.git
cd nemotron-csharp-testgen
```

### 2. Set Up Python Virtual Environment
```bash
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure AI Agents (`.env`)
Create or edit your `.env` file:
```env
# Default Provider: "ollama" or "gemini"
AI_PROVIDER=ollama

# --- Option A: Local Ollama (Qwen 3 Coder) ---
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen3-coder:latest

# --- Option B: Google Gemini API ---
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_API_KEY_2=your_second_key_here
```

---

## ⚡ Quick Start

### Option A: Modern Web Dashboard (Recommended)

Launch the integrated UI server:
```bash
python run_ui.py
```
Open **`http://localhost:5000`** in your browser:
- **Scan Tab**: Browse or enter your .NET project path, scan AST, and view all discovered projects and types.
- **Test Generation Tab**: Run single-file test generation or trigger one-click **Batch Generation** with live streamed console output.
- **SonarQube Tab**: Connect to SonarQube, view cognitive complexity violations, and auto-refactor complex methods with safety rollbacks.

---

### Option B: Command Line Interface (CLI)

Run full solution test generation in two simple commands:

```bash
# 1. Scan the target .NET solution or project
python scan_project.py "C:\path\to\your\dotnet-project"

# 2. Automatically scaffold, generate, and self-heal all testable files
python batch_generate.py --resume
```

> **Note:** `batch_generate.py` **automatically scaffolds** the test project if it doesn't already exist. You do not need to run scaffolding manually.

---

## 📖 Autonomous Test Generation Workflow

### Step 1: Scan the Target Project
The scanner parses all `.cs` files via AST analysis and produces `scan_output.json`:
```bash
python scan_project.py "C:\path\to\your\project-or-solution"
```
Contains:
- Discovered projects and `.csproj` dependencies
- Full class, interface, record, and enum definitions
- Methods, constructors, parameter signatures, and DTO structures

---

### Step 2: Automated Scaffolding & Solution Isolation
When running `batch_generate.py` or `python scaffold_tests.py`, the scaffolder:
1. Creates `tests/<ProjectName>.Tests/` with the matching Target Framework (`net8.0`).
2. Adds references to `Moq`, `FluentAssertions`, and `Microsoft.EntityFrameworkCore.InMemory` (if EF Core is detected).
3. Adds `<FrameworkReference Include="Microsoft.AspNetCore.App" />` if the target is a Web project.
4. Links sibling `<ProjectReference>` dependencies in multi-project solutions.
5. Injects `.editorconfig` (`root = true`) and MSBuild analyzer suppressions (`RunAnalyzersDuringBuild=false`, `NoWarn=...`) to isolate tests from parent solution warnings (e.g. CA1707, StyleCop).
6. Grants `<InternalsVisibleTo>` to allow testing `internal` members.

---

### Step 3: Single-File or Solution-Wide Batch Generation

#### Single File Test Generation:
```bash
python generate_tests.py AuthService.cs --manifest scan_output.json --project WebApp
```

#### Batch Generation Across All Projects:
```bash
# Process all projects in the solution
python batch_generate.py --all-projects --resume

# Target a specific project
python batch_generate.py --project WebApp --resume

# Resume a single file through the batch engine
python batch_generate.py --only-file BookingsController.cs
```

---

### Step 4: Self-Healing Critic Loop & Full TRX Stack Traces

The **2-Agent Loop** operates iteratively until reaching target code coverage (default: **>= 90%**):

```
       +-------------------------------------------------------------+
       |                     Context Builder                         |
       |  (Target Code + DTOs + Interfaces + DbContext Context)     |
       +------------------------------+------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                       Author Agent                          |
       |   (Gemini: Writes/refines xUnit tests with exact folder     |
       |    mirroring, namespace matching, and controller context)   |
       +------------------------------+------------------------------+
                                      | Writes test file
                                      v
       +-------------------------------------------------------------+
       |                       Critic Agent                          |
       |  - Runs: dotnet test --collect:"XPlat Code Coverage"        |
       |  - Logs: VSTest TRX report (exact stack traces & errors)    |
       |  - Parses: Cobertura XML line coverage                     |
       +------------------------------+------------------------------+
                                      |
               +----------------------+----------------------+
               |                                             |
     [Coverage >= 90% & Pass]                   [Failures or Coverage < 90%]
               |                                             |
               v                                             v
            SUCCESS                          Feeds exact TRX stack traces,
      (Saves to checkpoint)                 compiler errors & uncovered lines
                                            back to Author Agent for retry
```

---

## 🔍 SonarQube Cognitive Complexity & Auto-Refactoring

This branch includes an automated refactoring engine for codebases with high cognitive complexity:

### CLI Usage:
```bash
python resolve_complexity.py \
  --project-path "C:\path\to\your\dotnet-project" \
  --threshold 15 \
  --model gemini-3.6-flash
```

### Features:
- **Rule Detection**: Identifies `csharpsquid:S3776` (Cognitive Complexity of methods should not be too high).
- **Safety First**: Automatically creates a `.bak` backup file and git checkpoint before modifying any source code.
- **Compilation Validation**: Automatically verifies `dotnet build` passes on the refactored code. If compilation fails, it automatically rolls back changes to preserve code integrity.
- **Web UI Integration**: Inspect and trigger refactoring actions directly from the SonarQube tab in the browser dashboard.

---

## 🔑 Resilient Multi-Key & Multi-Model Quota Handling

To avoid getting blocked by Google Gemini API free-tier quotas and transient server spikes:

1. **Multi-Key Rotation**:
   - The tool automatically registers all `GEMINI_API_KEY*` entries in `.env`.
   - On `429 RESOURCE_EXHAUSTED` (RateLimitError), it transparently rotates to the next available key.
2. **Model Fallbacks**:
   - If all keys reach quota for the primary model (`gemini-3.6-flash`), the Author Agent automatically tries fallback models (`gemini-3.5-flash-lite`, `gemini-2.5-flash`), which maintain independent quotas.
3. **Transient 503 Handling**:
   - If Google's API experiences temporary demand spikes (`503 Service Unavailable / High Demand`), the agent automatically backs off for 3 seconds, rotates keys, and retries without dropping files.
4. **Invalid Key Protection**:
   - Bypasses expired or malformed keys without crashing the batch.

---

## 🔄 Checkpointing & Zero-Cost Resume

All completed tests are atomically recorded into **`.testgen_state.json`**.

| Scenario | Behavior |
|---|---|
| **Interrupted Execution (Ctrl+C)** | Progress is saved. Re-running with `--resume` resumes instantly. |
| **Quota Exhaustion** | Safely exits with code 0. Progress is preserved in the checkpoint. |
| **Re-running on Finished Project** | Files with `>= 90%` coverage are checked via Critic and skipped with **0 LLM token cost**. |
| **Force Fresh Generation** | Run with `--force` to ignore the checkpoint and re-evaluate all files. |

---

## 📚 CLI Reference

### `batch_generate.py`
```text
python batch_generate.py [options]

  --manifest PATH       Path to scan manifest (default: scan_output.json)
  --project NAME        Target a specific project in multi-project solutions
  --all-projects        Process all projects in the solution sequentially
  --provider PROVIDER   AI provider: auto, ollama, gemini, qwen-cloud (default: auto)
  --model MODEL         Model name (default: auto-detected for selected provider)
  --coverage FLOAT      Target code coverage percentage (default: 90.0)
  --retries INT         Max Author/Critic repair iterations per file (default: 4)
  --concurrency INT     Parallel workers (default: 1)
  --only-file NAME      Process only a specific single file
  --resume              Skip files with existing passing tests >= target coverage
  --force               Ignore saved checkpoint and re-evaluate all files
  --no-skip             Do not auto-skip DTOs, enums, or migrations
```

### `generate_tests.py`
```text
python generate_tests.py <FileName.cs> [options]

  --manifest PATH       Path to scan manifest (default: scan_output.json)
  --project NAME        Target project name
  --provider PROVIDER   AI provider: auto, ollama, gemini, qwen-cloud (default: auto)
  --model MODEL         Model name (default: auto-detected for selected provider)
  --coverage FLOAT      Target code coverage percentage (default: 90.0)
  --retries INT         Max iterations (default: 4)
```

### `scan_project.py`
```text
python scan_project.py <path-to-solution-or-project>
```

### `run_ui.py`
```text
python run_ui.py [--port 5000]
```

---

## 🏗️ Architecture & How It Works

### 1. Test Generation Architecture & Workflow

```text
+---------------------------------------------------------------------------------------------------+
|                                       USER INTERFACES                                             |
|   Web UI Dashboard (http://localhost:5000)         OR       CLI (batch_generate.py / generate.py) |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                      BATCH ORCHESTRATOR                                           |
|  - Auto-scaffolds missing test projects (net8.0 + ASP.NET Core + Moq + FluentAssertions + EF Core)|
|  - Auto-skips non-testable files (DTOs, Enums, Migrations)                                        |
|  - Zero-cost checkpoint resume (.testgen_state.json)                                              |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                      TESTGEN AGENT LOOP                                           |
|                           Per-file orchestrator (up to N retries)                                 |
+-------------------------------------------------+-------------------------------------------------+
                         |                                          |
                         v                                          v
      +-------------------------------------+    +-------------------------------------+
      |            AUTHOR AGENT             |    |            CRITIC AGENT             |
      | - Local Ollama (Qwen 3 Coder)       |    | - Executes dotnet test with TRX     |
      |   OR Google Gemini (AI Studio)      |    | - Extracts full, untruncated stack  |
      |   OR Qwen Cloud (DashScope)         |    |   traces & compiler error codes     |
      | - Multi-Key failover for cloud keys |    | - Calculates line coverage via      |
      | - Generates xUnit + Moq tests       |    |   Cobertura XML                     |
      +------------------+------------------+    +------------------+------------------+
                         |                                          ^
                         |--------- Writes test file -------------->|
                         |<-------- TRX Stack Traces & Coverage ----|
                         |          (Iterative self-healing loop)
                         v
      +-------------------------------------------------------------------------------+
      |                                  COMPLETION                                   |
      |  Coverage >= 90% & All Tests Pass -> Saved to Checkpoint & Batch Report       |
      +-------------------------------------------------------------------------------+
```

---

### 2. SonarQube Cognitive Complexity Refactoring Workflow

```text
+---------------------------------------------------------------------------------------------------+
|                                 SONARQUBE REFACTORING STUDIO                                      |
|    Web UI Dashboard (Sonar Tab)                    OR         CLI (resolve_complexity.py)         |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                      SONARQUBE CLIENT                                             |
|  - Connects to SonarQube / SonarCloud API (URL + Token)                                           |
|  - Queries Cognitive Complexity rule violations (csharpsquid:S3776)                               |
|  - Identifies target files, offending methods, and complexity metrics                             |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                   SAFETY & BACKUP ENGINE                                          |
|  - Creates atomic timestamped backup (<File>.bak)                                                 |
|  - Records local Git checkpoint before code modification                                          |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                      REFACTOR AGENT                                               |
|  - Extracts full method AST from C# source code                                                   |
|  - Gemini LLM decomposes complex loops, nested switches, and conditional logic                    |
|  - Preserves exact method contracts, public APIs, and signatures                                  |
|  - Rewrites target file with cleanly extracted helper methods                                     |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                    COMPILATION VALIDATOR                                          |
|  - Runs: dotnet build <Project.csproj>                                                            |
|                                                                                                   |
|           [Build SUCCESS]                                    [Build FAILED]                       |
|                  |                                                 |                              |
|                  v                                                 v                              |
|        Commit changes & report                          Automated Rollback                        |
|        reduced complexity score                     (Restores from .bak / Git)                    |
|        to UI / batch report                         Zero breaking changes                         |
+---------------------------------------------------------------------------------------------------+
```

---

### 3. Project Directory Structure

```text
nemotron-csharp-testgen/
├── run_ui.py                 # Web UI launcher
├── ui_server.py              # HTTP backend & SSE real-time log streamer
├── ui/                       # Frontend SPA (Vanilla JS + Modern Dark CSS)
│   ├── index.html
│   ├── css/
│   └── js/
│
├── scan_project.py           # AST Scanner
├── scaffold_tests.py         # Standalone test project scaffolder
├── generate_tests.py         # Single file test generator CLI
├── batch_generate.py         # Autonomous batch orchestrator CLI
├── resolve_complexity.py     # SonarQube cognitive complexity refactoring CLI
│
├── testgen/                  # Core test generation engine
│   ├── author.py             # Author Agent (Gemini client, multi-key rotation, 503 retry)
│   ├── critic.py             # Critic Agent (TRX parser, Cobertura XML coverage)
│   ├── agent_loop.py         # Orchestration loop between Author and Critic
│   ├── context.py            # AST context assembler & dependency builder
│   ├── scanner.py            # Regex/AST C# code scanner
│   ├── scaffold.py           # Universal xUnit test project scaffolder
│   ├── state.py              # Checkpoint tracker (.testgen_state.json)
│   └── logger.py             # Centralized file and console logger
│
├── sonar/                    # SonarQube analysis & refactoring engine
│   ├── refactor_agent.py     # Method refactoring logic with safety backups
│   └── sonar_client.py       # SonarQube API client
│
├── walkthrough.md            # Comprehensive verification and test results
└── README.md                 # This documentation
```

---

## 📂 Folder Mirroring Convention

Generated tests **strictly mirror** the directory layout of the target project under the `tests/<ProjectName>.Tests/` directory:

```
Source Project (WebApp/)                    Test Project (tests/WebApp.Tests/)
------------------------                    ----------------------------------
Controllers/                                Controllers/
  AuthController.cs             --->          AuthControllerTest.cs
  ListTaskController.cs         --->          ListTaskControllerTest.cs
Services/                                   Services/
  CommentService/                             CommentService/
    CommentWebApiService.cs     --->            CommentWebApiServiceTest.cs
Helpers/                                    Helpers/
  HttpResponseHelper.cs         --->          HttpResponseHelperTest.cs
```

---

## 🔧 Troubleshooting

### Test Project Not Found
`batch_generate.py` and `ui_server.py` **auto-scaffold** missing test projects on the fly. If you prefer to manually pre-scaffold:
```bash
python scaffold_tests.py
```

### Rate Limits / Quotas
If you encounter `[ALL KEYS EXHAUSTED]`, your progress is safely saved to `.testgen_state.json`. Simply wait for your free-tier daily/minute quota to reset and run:
```bash
python batch_generate.py --resume
```

### Controller / MVC Tests Fail to Build
If controller tests require ASP.NET Core types, the scaffolder automatically ensures `<FrameworkReference Include="Microsoft.AspNetCore.App" />` is in the test project. Re-running `python scaffold_tests.py` ensures the framework reference is active.

---

## 📄 License

MIT License.
