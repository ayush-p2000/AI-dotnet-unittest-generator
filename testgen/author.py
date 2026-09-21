import os
import re
import time
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from openai import OpenAI

from testgen.logger import get_logger

load_dotenv()


def get_gemini_api_keys() -> list[str]:
    """Finds all GEMINI_API_KEY* defined in environment or .env."""
    keys = []
    # Check primary key
    main_k = os.environ.get("GEMINI_API_KEY")
    if main_k and main_k not in keys:
        keys.append(main_k)

    # Check additional numbered or named keys
    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY_") and v and v not in keys:
            keys.append(v)

    if not keys:
        raise ValueError("No GEMINI_API_KEY or GEMINI_API_KEY_* found in .env")
    return keys


def extract_csharp_code(text: str) -> str:
    """Extracts raw C# code from markdown code fences or raw text."""
    match = re.search(r"```(?:csharp|cs)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


class AllKeysRateLimitedError(Exception):
    """Raised when all available Gemini API keys have exhausted their rate limit."""
    pass


class AuthorAgent:
    def __init__(self, model_name: str = "gemini-3.6-flash"):
        self.logger = get_logger()
        self.api_keys = get_gemini_api_keys()
        self.clients = [
            OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=k,
            )
            for k in self.api_keys
        ]
        self.current_client_idx = 0
        self.model_name = model_name
        self.logger.info(f"[INFO] AuthorAgent initialized with {len(self.clients)} API key(s) (Model: {model_name}).")

    def _rotate_client(self) -> OpenAI:
        self.current_client_idx = (self.current_client_idx + 1) % len(self.clients)
        self.logger.info(f"   [KEY ROTATION] Switched to Gemini API Key #{self.current_client_idx + 1}")
        return self.clients[self.current_client_idx]

    def generate_tests(
        self,
        context: Dict[str, Any],
        critic_feedback: Optional[Dict[str, Any]] = None,
        previous_code: Optional[str] = None,
    ) -> str:
        """
        Generates or refines unit test code for the target C# file using Gemini.
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
            "   - Mock all injected interfaces (e.g., ILogger<T>, IServiceProvider, custom domain interfaces) using Moq (`new Mock<TInterface>()`).\n"
            "   - For async methods returning Task or ValueTask, use `.ReturnsAsync(...)` on mocked setups.\n"
            "   - Support CancellationToken (e.g., `It.IsAny<CancellationToken>()` or `CancellationToken.None`).\n"
            "4. DbContext & EF Core Entities (if applicable):\n"
            "   - If testing a class that depends on DbContext, use DbContextOptionsBuilder<TContext> with UseInMemoryDatabase(Guid.NewGuid().ToString()) so every test gets an isolated in-memory DB.\n"
            "   - When creating EF model entities, initialise ALL `required` properties shown in the dependency context. Pay close attention to enum types, navigation properties, and validation logic visible in the entity source code.\n"
            "5. High Coverage & Edge Cases: Cover all public and internal methods, happy paths, null/invalid arguments (verify ArgumentNullException / ArgumentException), empty collections, non-matching IDs, exception flows, and every conditional branch.\n"
            "6. Syntax & References: All mock setups, method names, and DTO properties must strictly match the definitions in the Context. Do not invent non-existent properties or methods.\n"
            "7. Output Format: Return ONLY valid, complete C# code within a single ```csharp ... ``` code block. Do not include extra conversational text outside the code block."
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

        # Try each available API key once for rate-limiting.
        num_keys = len(self.clients)
        rate_limited_count = 0

        for attempt in range(1, num_keys + 1):
            client = self.clients[self.current_client_idx]
            try:
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                )
                raw_content = response.choices[0].message.content or ""
                return extract_csharp_code(raw_content)
            except Exception as e:
                err_str = str(e)
                # Rate limit / Quota errors -> rotate to next key
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "RateLimitError" in err_str:
                    rate_limited_count += 1
                    self.logger.warning(f"   [RATE LIMIT] Key #{self.current_client_idx + 1} reached quota limit.")
                    if rate_limited_count < num_keys:
                        self._rotate_client()
                        time.sleep(1.0)
                        continue
                    else:
                        self.logger.error("   [ALL KEYS EXHAUSTED] All provided Gemini API keys have reached their quota limits.")
                        raise AllKeysRateLimitedError("All Gemini API keys reached rate limit quota.")
                else:
                    # Bug 7 fix: Non-rate-limit errors are not key-dependent; log and raise immediately
                    self.logger.error(f"   [API ERROR] Non-rate-limit error: {e}")
                    raise

        raise AllKeysRateLimitedError("All Gemini API keys reached rate limit quota.")
