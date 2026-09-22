import os
import re
import time
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from openai import OpenAI

from testgen.logger import get_logger

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


def get_qwen_api_keys() -> List[str]:
    """Finds all QWEN_API_KEY* or DASHSCOPE_API_KEY defined in environment or .env."""
    keys = []
    main_k = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if main_k and main_k not in keys:
        keys.append(main_k)

    for k, v in os.environ.items():
        if (k.startswith("QWEN_API_KEY_") or k.startswith("DASHSCOPE_API_KEY_")) and v and v not in keys:
            keys.append(v)

    if not keys:
        raise ValueError("No QWEN_API_KEY or DASHSCOPE_API_KEY found in .env for Qwen Cloud")
    return keys


def resolve_provider(provider: Optional[str] = None, model_name: Optional[str] = None) -> str:
    """
    Determines whether to use 'ollama' (Local Qwen 3 Coder), 'gemini', or 'qwen-cloud'.
    """
    if provider and provider.lower() not in ["auto", ""]:
        return provider.lower()

    if model_name:
        m = model_name.lower()
        if m.startswith("gemini"):
            return "gemini"
        if m.startswith("qwen") or "coder" in m:
            # If QWEN_API_KEY is present and OLLAMA_BASE_URL not set, could be cloud,
            # but user has local Ollama installed, so default to ollama unless specified.
            if os.environ.get("AI_PROVIDER") == "qwen-cloud":
                return "qwen-cloud"
            return "ollama"

    return os.environ.get("AI_PROVIDER", "ollama").lower()


def extract_csharp_code(text: str) -> str:
    """Extracts raw C# code from markdown code fences or raw text."""
    match = re.search(r"```(?:csharp|cs)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


class AllKeysRateLimitedError(Exception):
    """Raised when all available API keys have exhausted their rate limit."""
    pass


class AuthorAgent:
    """
    Multi-Provider Unit Test Author Agent.
    Supports:
      - 'ollama': Local Qwen 3 Coder (http://localhost:11434/v1)
      - 'gemini': Google Gemini API with multi-key failover rotation
      - 'qwen-cloud': Alibaba Cloud DashScope / OpenRouter OpenAI-compatible endpoint
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.logger = get_logger()
        self.provider = resolve_provider(provider=provider, model_name=model_name)

        if self.provider == "ollama":
            self.base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            self.model_name = model_name or os.environ.get("OLLAMA_MODEL", "qwen3-coder:latest")
            api_key = os.environ.get("OLLAMA_API_KEY", "ollama")
            self.api_keys = [api_key]
            self.clients = [OpenAI(base_url=self.base_url, api_key=api_key)]
            self.fallback_models = []
            self.logger.info(f"[INFO] AuthorAgent initialized with Ollama (Model: {self.model_name}, Endpoint: {self.base_url}).")

        elif self.provider == "qwen-cloud":
            self.base_url = os.environ.get("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            self.model_name = model_name or "qwen3-coder-plus"
            self.api_keys = get_qwen_api_keys()
            self.clients = [OpenAI(base_url=self.base_url, api_key=k) for k in self.api_keys]
            self.fallback_models = ["qwen3-coder-next"]
            self.logger.info(f"[INFO] AuthorAgent initialized with Qwen Cloud ({len(self.clients)} key(s), Model: {self.model_name}).")

        else:  # "gemini"
            self.provider = "gemini"
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            self.model_name = model_name or "gemini-3.6-flash"
            self.api_keys = get_gemini_api_keys()
            self.clients = [OpenAI(base_url=self.base_url, api_key=k) for k in self.api_keys]
            self.fallback_models = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
            self.logger.info(f"[INFO] AuthorAgent initialized with Gemini ({len(self.clients)} key(s), Model: {self.model_name}).")

        self.current_client_idx = 0

    def _rotate_client(self) -> OpenAI:
        self.current_client_idx = (self.current_client_idx + 1) % len(self.clients)
        self.logger.info(f"   [KEY ROTATION] Switched to {self.provider.upper()} API Key #{self.current_client_idx + 1}")
        return self.clients[self.current_client_idx]

    def generate_tests(
        self,
        context: Dict[str, Any],
        critic_feedback: Optional[Dict[str, Any]] = None,
        previous_code: Optional[str] = None,
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
            "You are an expert .NET 8 / C# Unit Test Engineer specializing in xUnit, Moq, and FluentAssertions.\n"
            "Your task is to write comprehensive, production-grade unit tests for the provided C# file to achieve >= 90% code coverage.\n\n"
            "Guidelines:\n"
            "1. Frameworks & Naming: Use xUnit ([Fact], [Theory], [InlineData]), Moq (Mock<T>, It.IsAny<T>(), Setup, Verify), and FluentAssertions (.Should().Be(), .Should().ThrowAsync<T>(), .Should().NotBeNull()). Name the test class `public class <TargetFileNameWithoutExtension>Test`.\n"
            "2. NAMESPACE (CRITICAL): You MUST use the exact test namespace provided in the prompt (labeled 'Test Namespace'). "
            "Do NOT derive the test namespace from the source namespace. Do NOT append '.Tests' to the source namespace. "
            "The test namespace is pre-computed to match the test project's folder structure.\n"
            "3. Dependency Injection & Mocking:\n"
            "   - Only mock interfaces (e.g., `Mock<IMyService>()`) or virtual/abstract methods. Do NOT attempt to mock non-virtual methods of concrete or sealed classes (Moq will throw NotSupportedException). For concrete classes, instantiate them directly with test data.\n"
            "   - For async methods returning Task<T> or ValueTask<T>, use `.ReturnsAsync(...)`. For Task, use `.Returns(Task.CompletedTask)`.\n"
            "   - For async methods with CancellationToken, use `It.IsAny<CancellationToken>()` in setups.\n"
            "   - For `ILogger<T>`, use `Mock.Of<ILogger<T>>()` or `new Mock<ILogger<T>>()`; do not attempt strict verification on ILogger extension methods.\n"
            "4. DbContext & EF Core Entities (if applicable):\n"
            "   - If testing a class that depends on DbContext, use DbContextOptionsBuilder<TContext> with UseInMemoryDatabase(Guid.NewGuid().ToString()) so every test gets an isolated in-memory DB.\n"
            "   - When creating EF model entities, initialise ALL `required` and non-nullable properties shown in the dependency context. Pay close attention to enum types, navigation properties, and validation logic visible in the entity source code.\n"
            "5. ASP.NET Core Controllers & APIs (if testing a Controller or ControllerBase):\n"
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
            "6. Factory Methods & Private Constructors:\n"
            "   - Check if the target class or DTO uses private constructors with static factory methods (e.g., `Result.Success(...)`, `Result.Error(...)`). Use the factory methods instead of calling private constructors.\n"
            "7. High Coverage & Edge Cases: Cover all public and internal methods, happy paths, null/invalid arguments (verify ArgumentNullException / ArgumentException), empty collections, non-matching IDs, exception flows, and every conditional branch.\n"
            "8. Syntax & References: All mock setups, method names, and DTO properties must strictly match the definitions in the Context. Do not invent non-existent properties or methods.\n"
            "9. Output Format: Return ONLY valid, complete C# code within a single ```csharp ... ``` code block. Do not include extra conversational text outside the code block."
        )

        user_content_parts = [
            f"## Target File to Test: `{file_name}`",
            f"Source Namespace: `{source_namespace}`",
            f"Test Namespace (USE THIS EXACTLY): `{test_namespace}`\n",
            "### Target File Source Code:\n```csharp\n" + source_code + "\n```\n",
            "### Context & Resolved Dependencies (DTOs, Interfaces to Mock, Enums, DbContext):\n" + dependencies_context + "\n"
        ]

        if critic_feedback and previous_code:
            user_content_parts.extend([
                "---",
                "## CRITIC FEEDBACK & EXISTING TEST CODE TO REFINE:",
                f"Status: {critic_feedback.get('status')}",
                f"Coverage achieved so far: {critic_feedback.get('coverage_pct', 0.0):.1f}%\n",
                f"Issues / Failure Details / Missing Lines:\n{critic_feedback.get('feedback_message', 'Please review and fix errors.')}\n",
                "### Existing Test Code:\n```csharp\n" + previous_code + "\n```\n",
                "Please fix the compiler errors / failing tests and add missing test methods for uncovered branches. Output the COMPLETE updated C# test file."
            ])
        else:
            user_content_parts.append(
                "Generate the complete C# xUnit test file for this class now."
            )

        user_prompt = "\n".join(user_content_parts)

        # Ollama local execution
        if self.provider == "ollama":
            client = self.clients[0]
            try:
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    timeout=120.0,
                )
                raw_content = response.choices[0].message.content or ""
                return extract_csharp_code(raw_content)
            except Exception as e:
                err_str = str(e)
                if "Connection error" in err_str or "connection refused" in err_str.lower():
                    self.logger.error(
                        f"   [OLLAMA ERROR] Cannot connect to Ollama at {self.base_url}. "
                        "Please ensure the Ollama service is running ('ollama serve')."
                    )
                elif "model" in err_str.lower() and "not found" in err_str.lower():
                    self.logger.error(
                        f"   [OLLAMA ERROR] Model '{self.model_name}' not found. "
                        f"Run 'ollama pull {self.model_name}' to install it."
                    )
                else:
                    self.logger.error(f"   [OLLAMA ERROR] {e}")
                raise

        # Cloud-based providers (Gemini / Qwen Cloud) with key rotation
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
                    response = client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                        timeout=120.0,
                    )
                    raw_content = response.choices[0].message.content or ""
                    return extract_csharp_code(raw_content)
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
