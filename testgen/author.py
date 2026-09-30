import os
import re
import time
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from openai import OpenAI

from testgen.logger import get_logger, log_prompt_details, log_ai_result_details

load_dotenv()


def get_gemini_api_keys() -> List[str]:
    """Finds all GEMINI_API_KEY* defined in environment or .env."""
    keys = []
    main_k = os.environ.get("GEMINI_API_KEY")
    if main_k and main_k not in keys:
        keys.append(main_k)

    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY_") and v and v not in keys:
            keys.append(v)

    if not keys:
        raise ValueError("No GEMINI_API_KEY or GEMINI_API_KEY_* found in .env")
    return keys


def resolve_provider(provider: Optional[str] = None, model_name: Optional[str] = None) -> str:
    """Always resolves to 'gemini'."""
    return "gemini"


def extract_csharp_code(text: str) -> str:
    """
    Extracts raw C# code from markdown code fences or raw text.
    Handles truncated responses (unclosed fences), stray backticks,
    conversational commentary, and incomplete syntax / unbalanced braces.
    """
    if not text or not text.strip():
        return ""

    # 1. Match code fence (with or without closing fence)
    # Allows truncated outputs where ``` was never closed
    fence_pattern = re.search(r"```(?:csharp|cs)?\s*\n?([\s\S]*?)(?:```|$)", text, re.IGNORECASE)
    if fence_pattern:
        extracted = fence_pattern.group(1).strip()
    else:
        extracted = text.strip()

    # 2. Strip any accidental leading/trailing backtick remnants
    extracted = re.sub(r"^```(?:csharp|cs)?\s*", "", extracted, flags=re.IGNORECASE)
    extracted = re.sub(r"```\s*$", "", extracted)

    # 3. Strip leading conversational text before the first C# code line
    # Look for using, namespace, attributes, comments, or class
    first_code = re.search(
        r"^(?=\s*(?:using\s+[A-Za-z0-9_.]+|namespace\s+[A-Za-z0-9_.]+|\[\s*(?:Fact|Theory|assembly|TestFixture)|//|/\*))",
        extracted,
        re.MULTILINE
    )
    if first_code:
        extracted = extracted[first_code.start():].strip()

    # 4. Strip any non-C# conversational chatter after the last closing brace
    last_brace = extracted.rfind("}")
    if last_brace != -1:
        after_brace = extracted[last_brace + 1:].strip()
        # If text after last brace has no C# syntax (no semicolons, braces, or keywords), discard it
        if after_brace and not any(kw in after_brace for kw in [";", "{", "}", "class", "namespace", "using"]):
            extracted = extracted[:last_brace + 1].strip()

    # 5. Handle truncation mid-code: repair incomplete statements and balance braces
    lines = extracted.splitlines()
    # If the last line is cut off mid-statement (does not end with ;, }, or {)
    while lines and lines[-1].strip() and not lines[-1].strip().endswith((";", "}", "{")):
        lines.pop()

    current_code = "\n".join(lines)
    open_braces = current_code.count("{")
    close_braces = current_code.count("}")

    if open_braces > close_braces:
        missing = open_braces - close_braces
        lines.append("\n// [AUTO-REPAIR] Added closing braces for truncated output")
        for _ in range(missing):
            lines.append("}")
        extracted = "\n".join(lines)
    else:
        extracted = current_code

    return extracted.strip()


class AllKeysRateLimitedError(Exception):
    """Raised when all available API keys have exhausted their rate limit."""
    pass


class AuthorAgent:
    """
    Google Gemini Unit Test Author Agent with automated multi-key rotation and failover.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.logger = get_logger()
        self.provider = "gemini"
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        self.api_keys = get_gemini_api_keys()
        self.clients = [OpenAI(base_url=self.base_url, api_key=k) for k in self.api_keys]
        self.fallback_models = ["gemini-3.6-flash"]
        self.logger.info(
            f"[INFO] AuthorAgent initialized with Google Gemini ({len(self.clients)} key(s), Model: {self.model_name})."
        )

        self.current_client_idx = 0

    def _rotate_client(self) -> OpenAI:
        self.current_client_idx = (self.current_client_idx + 1) % len(self.clients)
        self.logger.info(f"   [KEY ROTATION] Switched to Gemini API Key #{self.current_client_idx + 1}")
        return self.clients[self.current_client_idx]

    def generate_tests(
        self,
        context: Dict[str, Any],
        critic_feedback: Optional[Dict[str, Any]] = None,
        previous_code: Optional[str] = None,
        iteration: int = 1,
        memory_prompt: Optional[str] = None,
    ) -> str:
        """
        Generates or refines unit test code for the target C# file using the configured AI agent.
        Works generically for any .NET 8 C# project.
        """
        file_name = context["file_name"]
        source_code = context["source_code"]
        source_namespace = context["namespace"] or "Tests"
        test_namespace = context.get("test_namespace", f"{source_namespace}.Tests")
        dependencies_context = context.get("formatted_dependencies_context", "No additional dependencies.")

        system_prompt = (
            "You are an expert modern .NET (.NET 8, .NET 9+) / C# (C# 12 / 13) Unit Test Engineer specializing in xUnit, Moq, and FluentAssertions.\n"
            "Your task is to write comprehensive, production-grade unit tests for the provided C# file to achieve >= 90% code coverage.\n\n"
            "Guidelines:\n"
            "1. Frameworks & Naming: Use xUnit ([Fact], [Theory], [InlineData]), Moq (Mock<T>, It.IsAny<T>(), Setup, Verify), and FluentAssertions (.Should().Be(), .Should().ThrowAsync<T>(), .Should().NotBeNull()). Name the test class `public class <TargetFileNameWithoutExtension>Test`.\n"
            "2. Modern C# (C# 12 / C# 13 & .NET 8 / 9+):\n"
            "   - PRIMARY CONSTRUCTORS: Target classes may use C# 12 primary constructors (e.g. `public class OrderService(IRepository repo, ILogger<OrderService> logger) : IOrderService`). Instantiate them directly in test setups: `new OrderService(_repoMock.Object, _loggerMock.Object)`.\n"
            "   - REQUIRED MEMBERS: If a class or DTO defines `required` properties or init-only setters, you MUST set all required members during object creation in object initializers (e.g., `new UserDto { Id = Guid.NewGuid(), Name = \"Test\" }`). Missing required members will trigger compiler error CS9035.\n"
            "   - COLLECTION EXPRESSIONS: Prefer modern C# collection expressions (e.g., `[]`, `[item1, item2]`) or standard collections when initializing lists/arrays and setting up mock returns (`.ReturnsAsync([])`).\n"
            "   - TIMEPROVIDER: If the class injects `System.TimeProvider` (the standard .NET 8+ time abstraction), mock it (`new Mock<TimeProvider>()`) and set up `.Setup(t => t.GetUtcNow()).Returns(...)`, or use `TimeProvider.System`.\n"
            "   - FILE-SCOPED NAMESPACES: You may use either file-scoped namespaces (`namespace Foo.Tests;`) or block namespaces, matching standard modern C# style.\n"
            "   - RAW STRING LITERALS: Use `\"\"\"...\"\"\"` for multiline strings or JSON payloads to avoid escaping.\n"
            "3. NAMESPACE (CRITICAL): You MUST use the exact test namespace provided in the prompt (labeled 'Test Namespace'). "
            "Do NOT derive the test namespace from the source namespace. Do NOT append '.Tests' to the source namespace. "
            "The test namespace is pre-computed to match the test project's folder structure.\n"
            "4. Dependency Injection & Mocking:\n"
            "   - Only mock interfaces (e.g., `Mock<IMyService>()`) or virtual/abstract methods. Do NOT attempt to mock non-virtual methods of concrete or sealed classes (Moq will throw NotSupportedException). For concrete classes, instantiate them directly with test data.\n"
            "   - RECORDS & DTOs: Never mock `record`, `record struct`, or DTO classes. Instantiate them directly with valid test data.\n"
            "   - STATIC EXTENSION METHODS (CRITICAL): In C#, static extension methods (such as `_settingsManager.GetValueAsync<T>()`, `IQueryable` extension methods, or `Session.SetString`) CANNOT be mocked with Moq (Moq throws System.NotSupportedException). Always check the interface definition in Context: only mock the real declared interface methods (for instance, mock `ISettingsManager.GetObjectSettingAsync()` rather than `GetValueAsync()`).\n"
            "   - METHOD OVERLOADS (CRITICAL): When mocking overloaded methods (e.g. `SignInManager.ExternalLoginSignInAsync`), match the EXACT parameter count and types called in the source code. If the class calls `ExternalLoginSignInAsync(p, k, false)` (3 arguments), your mock setup MUST be 3 arguments. Setting up the 4-argument overload will cause Moq to miss the call and return default values.\n"
            "   - For async methods returning Task<T> or ValueTask<T>, use `.ReturnsAsync(...)`. For Task, use `.Returns(Task.CompletedTask)`.\n"
            "   - For async methods with CancellationToken, use `It.IsAny<CancellationToken>()` in setups.\n"
            "   - For `ILogger<T>`, use `Mock.Of<ILogger<T>>()` or `new Mock<ILogger<T>>()`; do not attempt strict verification on ILogger extension methods.\n"
            "   - HTTP CLIENTS: If mocking `HttpClient` or `IHttpClientFactory`, mock `HttpMessageHandler` using `.Protected().Setup<Task<HttpResponseMessage>>(\"SendAsync\", ...)` or pass a custom `HttpMessageHandler`.\n"
            "5. DbContext & EF Core Entities (if applicable):\n"
            "   - If testing a class that depends on DbContext, use DbContextOptionsBuilder<TContext> with UseInMemoryDatabase(Guid.NewGuid().ToString()) so every test gets an isolated in-memory DB.\n"
            "   - When creating EF model entities, initialise ALL `required` and non-nullable properties shown in the dependency context. Pay close attention to enum types, navigation properties, and validation logic visible in the entity source code.\n"
            "6. ASP.NET Core & Web APIs (if testing a Controller, Minimal API endpoint, or Middleware):\n"
            "   - When testing classes that inherit from `Controller` or return `View()`, `RedirectToAction()`, or use `TempData`/`ModelState`:\n"
            "     Always configure `ControllerContext` and `TempData` to avoid InvalidOperationException on ITempDataDictionaryFactory:\n"
            "     ```csharp\n"
            "     var httpContext = new DefaultHttpContext();\n"
            "     var tempData = new TempDataDictionary(httpContext, Mock.Of<ITempDataProvider>());\n"
            "     var controller = new YourController(...) {\n"
            "         ControllerContext = new ControllerContext { HttpContext = httpContext },\n"
            "         TempData = tempData\n"
            "     };\n"
            "     ```\n"
            "     Ensure necessary usings are present: `using Microsoft.AspNetCore.Http;` `using Microsoft.AspNetCore.Mvc;` `using Microsoft.AspNetCore.Mvc.ViewFeatures;`\n"
            "7. Factory Methods & Private Constructors:\n"
            "   - Check if the target class or DTO uses private constructors with static factory methods (e.g., `Result.Success(...)`, `Result.Error(...)`). Use the factory methods instead of calling private constructors.\n"
            "8. High Coverage & Edge Cases: Cover all public and internal methods, happy paths, null/invalid arguments (verify ArgumentNullException / ArgumentException), empty collections, non-matching IDs, exception flows, and every conditional branch.\n"
            "9. Syntax & References: All mock setups, method names, and DTO/record properties must strictly match the definitions in the Context. Do not invent non-existent properties or methods.\n"
            "   - When verifying message bus events (e.g. `publishEndpoint.Publish<T>()`), use `_publishEndpointMock.Verify(p => p.Publish(It.IsAny<T>(), It.IsAny<CancellationToken>()), ...)` unless the exact record properties are provided in Context.\n"
            "   - When arranging domain entities with collection properties (e.g. `wallet.Assets`), always initialize the collection (e.g. `Assets = new List<Asset>()` or `Assets = []`) rather than leaving it null, because domain methods often invoke `.Add()` directly on navigation collections.\n"
            "10. Large Files & Function-by-Function Organization:\n"
            "    - For large classes with multiple methods, organize test methods modularly function-by-function using `#region <FunctionName> Tests` and `#endregion`.\n"
            "    - During refinement iterations, PRESERVE all existing passing test methods. Do not discard or rewrite already passing test cases—only add missing tests for uncovered branches or fix broken ones.\n"
            "    - TYPE NAME DISAMBIGUATION (CRITICAL): If the application declares a type that shadows a .NET BCL type (e.g. `Process`, `Timer`, `Task`), ALWAYS use the fully qualified global name (e.g. `global::System.Diagnostics.Process` or `using SysProcess = System.Diagnostics.Process;`) to prevent compiler CS1061 errors.\n"
            "11. Output Format: Return ONLY valid, complete C# code within a single ```csharp ... ``` code block. "
            "Do NOT include markdown backticks inside the C# code itself. "
            "Do NOT include introductory or concluding conversational text. "
            "Ensure ALL open braces '{' have corresponding closing braces '}'.\n"
            "12. IDisposable, Component & Finalizers Safety (CRITICAL - PREVENT TEST HOST CRASH):\n"
            "    - NEVER throw exceptions inside `Dispose(bool disposing)` or finalizers of classes deriving from `IDisposable` or `System.ComponentModel.Component` (such as `System.Diagnostics.Process`). In modern .NET, unhandled exceptions on the CLR finalizer thread (`System.GC.RunFinalizers()`) abruptly crash the entire `testhost.dll` process and abort the test run!\n"
            "    - If you need to simulate an exception thrown during `Dispose()`, only throw if `disposing == true` (`if (disposing) throw new ...`), AND call `GC.SuppressFinalize(this)` in the mock/subclass constructor so the CLR garbage collector does not run the finalizer."
        )

        from testgen.chunker import (
            extract_method_chunks,
            prioritize_methods,
            build_priority_blueprint,
            build_optimized_target_code,
        )

        # Retrieve or extract method chunks
        method_chunks = context.get("method_chunks")
        if not method_chunks:
            method_chunks = extract_method_chunks(source_code)

        uncovered_lines = critic_feedback.get("uncovered_lines", []) if critic_feedback else []
        test_failures = critic_feedback.get("test_failures", []) if critic_feedback else []

        prioritized_chunks = prioritize_methods(
            method_chunks,
            uncovered_lines=uncovered_lines,
            failing_tests=test_failures,
        )
        priority_blueprint = build_priority_blueprint(prioritized_chunks)

        is_refinement = bool(critic_feedback and previous_code)
        # In refinement mode, focus on uncovered methods and omit already-covered method bodies
        optimized_source_code = build_optimized_target_code(
            source_code,
            prioritized_chunks,
            focus_mode=is_refinement,
        )

        critical_targets = [c.name for c in prioritized_chunks if c.priority_score >= 4]
        if critical_targets:
            self.logger.info(f"   [CHUNKER] Priority method targets for {file_name}: {', '.join(critical_targets)}")

        is_large_file = len(prioritized_chunks) > 4 or len(source_code) > 6000
        user_content_parts = [
            f"## Target File to Test: `{file_name}`",
            f"Source Namespace: `{source_namespace}`",
            f"Test Namespace (USE THIS EXACTLY): `{test_namespace}`\n",
            "### Priority Coverage Plan (Target Functions to Cover):\n" + (priority_blueprint or "Cover all public methods.") + "\n",
            "### Target File Source Code:\n```csharp\n" + optimized_source_code + "\n```\n",
            "### Context & Resolved Dependencies (DTOs, Interfaces to Mock, Enums, DbContext):\n" + dependencies_context + "\n"
        ]

        if is_large_file:
            user_content_parts.append(
                "### LARGE FILE NOTICE - SPLIT FUNCTION-BY-FUNCTION:\n"
                "This target class has multiple functions. Structure your unit tests function-by-function using `#region <FunctionName> Tests` blocks.\n"
                "Prioritize writing test methods for the highest-priority functions listed above. Keep all tests modular and self-contained.\n"
            )

        if memory_prompt:
            user_content_parts.extend([
                "---",
                memory_prompt,
            ])

        if is_refinement:
            # Ensure previous_code is thoroughly stripped of any markdown fences to prevent nested fences
            clean_previous_code = extract_csharp_code(previous_code) if previous_code else ""
            user_content_parts.extend([
                "---",
                "## CRITIC FEEDBACK & EXISTING TEST CODE TO REFINE:",
                f"Status: {critic_feedback.get('status')}",
                f"Coverage achieved so far: {critic_feedback.get('coverage_pct', 0.0):.1f}%\n",
                f"Issues / Failure Details / Missing Lines:\n{critic_feedback.get('feedback_message', 'Please review and fix errors.')}\n",
                "### Existing Test Code:\n```csharp\n" + clean_previous_code + "\n```\n",
                "Please fix the compiler errors / failing tests and add missing test methods for uncovered branches. Preserve all existing working tests and output the COMPLETE updated C# test file."
            ])
        else:
            user_content_parts.append(
                "Generate the complete C# xUnit test file for this class now. Cover the high-priority methods first."
            )

        user_prompt = "\n".join(user_content_parts)

        # Context & token limits for Google Gemini
        context_limit_str = (
            os.environ.get("CONTEXT_LIMIT")
            or os.environ.get("MAX_CONTEXT_TOKENS", "32768")
        )
        try:
            context_limit = int(context_limit_str) if context_limit_str else 32768
        except ValueError:
            context_limit = 32768

        max_output_tokens = (
            os.environ.get("MAX_OUTPUT_TOKENS")
            or os.environ.get("MAX_TOKENS", "16384")
        )
        try:
            max_tokens_val = int(max_output_tokens) if max_output_tokens else 16384
        except ValueError:
            max_tokens_val = 16384

        # Check total estimated tokens and ensure prompt fits within common CONTEXT_LIMIT for all models
        est_tokens = int((len(system_prompt) + len(user_prompt)) / 3.5)
        if est_tokens > context_limit and dependencies_context:
            available_dep_chars = max(1000, int((context_limit - est_tokens + len(dependencies_context) / 3.5) * 3.5))
            if len(dependencies_context) > available_dep_chars:
                trimmed_deps = dependencies_context[:available_dep_chars] + "\n\n// ... [Dependencies trimmed to fit CONTEXT_LIMIT]"
                user_content_parts[4] = f"### Context & Resolved Dependencies (DTOs, Interfaces to Mock, Enums, DbContext):\n{trimmed_deps}\n"
                user_prompt = "\n".join(user_content_parts)
                self.logger.info(f"   [CONTEXT LIMIT] Trimmed prompt dependencies to stay within common CONTEXT_LIMIT={context_limit} tokens.")

        self.logger.info(f"   [INFO] Generation request for {self.provider.upper()} ({self.model_name}) | CONTEXT_LIMIT: {context_limit} tokens | MAX_OUTPUT_TOKENS: {max_tokens_val}")

        # Log complete prompt details (console summary, file dump, audit file)
        log_prompt_details(
            target_file=file_name,
            iteration=iteration,
            model_name=self.model_name,
            provider=self.provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            est_tokens=est_tokens,
        )

        # Google Gemini execution with automatic multi-key rotation
        models_to_try = [self.model_name]
        # Only use fallback models if explicitly enabled via environment variable
        if os.environ.get("ENABLE_MODEL_FALLBACK", "false").lower() in ("true", "1"):
            for fm in self.fallback_models:
                if fm not in models_to_try:
                    models_to_try.append(fm)

        for current_model in models_to_try:
            num_keys = len(self.clients)
            rate_limited_count = 0

            for attempt in range(1, num_keys + 1):
                client = self.clients[self.current_client_idx]
                key_num = self.current_client_idx + 1
                try:
                    create_params = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                        "timeout": 180.0,
                    }
                    if max_tokens_val:
                        create_params["max_tokens"] = max_tokens_val

                    # Configure reasoning effort (thinking tokens) if set or default to low for fast complete code
                    reasoning_effort = os.environ.get("GEMINI_REASONING_EFFORT", "low").strip()
                    if reasoning_effort and reasoning_effort.lower() != "default":
                        create_params["reasoning_effort"] = reasoning_effort.lower()

                    call_start = time.time()
                    try:
                        response = client.chat.completions.create(**create_params)
                    except Exception as call_err:
                        # Fallback if specific model or API version rejects reasoning_effort parameter
                        if "reasoning_effort" in create_params and ("reasoning_effort" in str(call_err).lower() or "400" in str(call_err)):
                            create_params.pop("reasoning_effort", None)
                            response = client.chat.completions.create(**create_params)
                        else:
                            raise
                    elapsed = time.time() - call_start

                    # Check finish reason for truncation
                    finish_reason = response.choices[0].finish_reason if response.choices else None
                    if finish_reason == "length":
                        self.logger.warning(
                            f"   [OUTPUT TRUNCATED] Model hit max_tokens limit ({max_tokens_val}). "
                            "Applying automatic syntax repair and brace balancing."
                        )

                    raw_content = response.choices[0].message.content or ""
                    extracted_code = extract_csharp_code(raw_content)

                    # Extract usage metadata if provided
                    usage_dict = None
                    if hasattr(response, "usage") and response.usage:
                        usage_dict = {
                            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(response.usage, "completion_tokens", None),
                            "total_tokens": getattr(response.usage, "total_tokens", None),
                        }

                    # Log complete result details
                    log_ai_result_details(
                        target_file=file_name,
                        iteration=iteration,
                        model_name=current_model,
                        provider=self.provider,
                        generated_code=extracted_code,
                        elapsed_sec=elapsed,
                        raw_response=raw_content,
                        usage=usage_dict,
                    )

                    return extracted_code
                except Exception as e:
                    err_str = str(e)
                    # Rate limit / Quota errors -> rotate to next key
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "RateLimitError" in err_str:
                        rate_limited_count += 1
                        self.logger.warning(f"   [RATE LIMIT] Key #{key_num} reached quota limit on {current_model}.")
                        if rate_limited_count < num_keys:
                            self._rotate_client()
                            time.sleep(1.0)
                            continue
                        else:
                            self.logger.warning(f"   [QUOTA EXHAUSTED] All keys exhausted quota for model {current_model}.")
                            break  # Try next fallback model
                    elif any(err_code in err_str for err_code in ["503", "502", "504", "500", "UNAVAILABLE"]):
                        self.logger.warning(f"   [SERVER BUSY/503] Service temporarily unavailable on {current_model}. Backing off 3s and retrying...")
                        time.sleep(3.0)
                        self._rotate_client()
                        continue
                    elif any(err_code in err_str for err_code in ["400", "401", "403", "API_KEY_INVALID"]):
                        self.logger.warning(f"   [INVALID KEY] Key #{key_num} returned authorization error. Rotating to next key...")
                        self._rotate_client()
                        continue
                    else:
                        self.logger.error(f"   [API ERROR] Unexpected error: {e}")
                        raise

        self.logger.error(f"   [ALL KEYS & MODELS EXHAUSTED] All provided {self.provider.upper()} API keys and models have reached their quota limits.")
        raise AllKeysRateLimitedError(f"All {self.provider.upper()} API keys and models reached rate limit quota.")
