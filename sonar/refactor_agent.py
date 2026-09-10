import os
import re
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from sonar.models import SonarIssue

load_dotenv()


def get_gemini_api_keys() -> List[str]:
    keys = []
    main_k = os.environ.get("GEMINI_API_KEY")
    if main_k and main_k not in keys:
        keys.append(main_k)

    for k, v in os.environ.items():
        if k.startswith("GEMINI_API_KEY_") and v and v not in keys:
            keys.append(v)

    if not keys:
        raise ValueError("No GEMINI_API_KEY or GEMINI_API_KEY_* found in environment or .env")
    return keys


def extract_csharp_code(text: str) -> str:
    """Extracts C# code from markdown fences or raw response."""
    match = re.search(r"```(?:csharp|cs)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


class RefactorAgent:
    """
    Generative Agent specialized in resolving SonarQube Cognitive Complexity (S3776).
    Decomposes monolithic methods into clean guard clauses, pattern matching, and private helper methods.
    """

    SYSTEM_PROMPT = """You are a Principal .NET 8 / C# Software Architect and Clean Code Refactoring Specialist.
Your task is to refactor a C# method to resolve a SonarQube Cognitive Complexity issue (rule csharpsquid:S3776).

# Core Principles of Cognitive Complexity Reduction:
1. Invert 'if' statements to use Guard Clauses & Early Returns. Flatten nested branches.
2. Decompose deep or complex logic into smaller, single-responsibility `private` helper methods within the same class.
3. Replace cascaded `if-else` chains with C# 8+ switch expressions, pattern matching, or lookup dictionaries.
4. Extract loop bodies or inner validation logic into private helper functions (nesting increments complexity exponentially).
5. Simplify boolean condition chains.

# Critical Constraints:
1. PRESERVE SIGNATURE: The refactored main method must have the EXACT same name, access modifier, parameters, return type, and async modifier.
2. ZERO BREAKING CHANGES: Maintain identical runtime semantics, exception behaviors, and business logic contracts.
3. HELPER METHODS: Any extracted helper methods must be `private` (or `private static` if they do not touch instance state).
4. SCOPE: Output ONLY the refactored main method and any new private helper methods needed. Do not wrap in class or namespace unless specifically instructed.
5. NO OMISSIONS: Do not use '// ... existing code ...'. Provide the complete, working code.
"""

    def __init__(self, model_name: str = "gemini-3.6-flash"):
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

    def _rotate_client(self) -> OpenAI:
        self.current_client_idx = (self.current_client_idx + 1) % len(self.clients)
        return self.clients[self.current_client_idx]

    @property
    def client(self) -> OpenAI:
        return self.clients[self.current_client_idx]

    def refactor_method(
        self,
        issue: SonarIssue,
        method_name: str,
        original_method_code: str,
        class_context: str,
        previous_errors: Optional[List[str]] = None,
        iteration: int = 1,
    ) -> str:
        """
        Invokes Gemini to refactor the target method, incorporating Sonar hotspot flows
        and any compilation/test failure feedback from previous iterations.
        """
        stats = issue.complexity_stats
        current_score = stats["current"]
        allowed_score = stats["allowed"]

        flows_text = ""
        if issue.flows:
            flows_text = "\nSonarQube Cognitive Complexity Hotspots:\n"
            for f in issue.flows:
                flows_text += f" - Line {f.line}: {f.msg}\n"

        prompt = f"""### Refactoring Request: Reduce Cognitive Complexity (S3776)

**Target Method**: `{method_name}`
**Current Cognitive Complexity**: {current_score}
**Required Maximum Complexity**: <= {allowed_score}
{flows_text}

### Enclosing Class Context:
```csharp
{class_context}
```

### Original Method Code to Refactor:
```csharp
{original_method_code}
```
"""

        if previous_errors:
            prompt += f"""
### ⚠️ PREVIOUS ATTEMPT FAILED (Iteration {iteration - 1})
The previous refactoring resulted in the following build or test errors. You MUST fix these:
```
{chr(10).join(previous_errors)}
```
"""

        prompt += f"""
### Instructions:
1. Refactor `{method_name}` to bring its Cognitive Complexity down to <= {allowed_score}.
2. Decompose into `private` helper methods if necessary.
3. Return ONLY the refactored method and any new private helper methods in a ```csharp ``` markdown code block.
"""

        # Retry loop across available API keys on rate limit
        max_attempts = len(self.clients) * 2
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                )
                raw_text = response.choices[0].message.content or ""
                return extract_csharp_code(raw_text)

            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate limit" in err_str.lower() or "quota" in err_str.lower():
                    time.sleep(2)
                    self._rotate_client()
                else:
                    raise

        raise RuntimeError("All Gemini API keys exhausted their rate limit during refactoring.")
