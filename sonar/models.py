from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import re


@dataclass
class TextRange:
    start_line: int
    end_line: int
    start_offset: int = 0
    end_offset: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["TextRange"]:
        if not data:
            return None
        return cls(
            start_line=data.get("startLine", 0),
            end_line=data.get("endLine", 0),
            start_offset=data.get("startOffset", 0),
            end_offset=data.get("endOffset", 0),
        )


@dataclass
class ComplexityFlow:
    """Represents secondary location markers showing where complexity increments."""
    msg: str
    line: int
    text_range: Optional[TextRange] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComplexityFlow":
        return cls(
            msg=data.get("msg", ""),
            line=data.get("textRange", {}).get("startLine", 0) if data.get("textRange") else 0,
            text_range=TextRange.from_dict(data.get("textRange")),
        )


@dataclass
class SonarIssue:
    key: str
    rule: str
    severity: str
    component: str
    project: str
    line: int
    message: str
    status: str
    effort: str = ""
    debt: str = ""
    text_range: Optional[TextRange] = None
    flows: List[ComplexityFlow] = field(default_factory=list)
    raw_json: Dict[str, Any] = field(default_factory=dict)

    @property
    def file_path(self) -> str:
        """Extracts relative file path from Sonar component key (e.g. 'project:path/to/File.cs')."""
        if ":" in self.component:
            return self.component.split(":", 1)[1]
        return self.component

    @property
    def complexity_stats(self) -> Dict[str, int]:
        """
        Parses SonarQube message: 'Refactor this method to reduce its Cognitive Complexity from X to the Y allowed.'
        Returns {'current': X, 'allowed': Y}.
        """
        match = re.search(r"Cognitive Complexity from (\d+) to the (\d+) allowed", self.message, re.IGNORECASE)
        if match:
            return {
                "current": int(match.group(1)),
                "allowed": int(match.group(2)),
            }
        return {"current": 0, "allowed": 15}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SonarIssue":
        text_range = TextRange.from_dict(data.get("textRange"))
        flows: List[ComplexityFlow] = []
        raw_flows = data.get("flows", [])
        for flow in raw_flows:
            locations = flow.get("locations", [])
            for loc in locations:
                flows.append(ComplexityFlow.from_dict(loc))

        return cls(
            key=data.get("key", ""),
            rule=data.get("rule", ""),
            severity=data.get("severity", ""),
            component=data.get("component", ""),
            project=data.get("project", ""),
            line=data.get("line", text_range.start_line if text_range else 0),
            message=data.get("message", ""),
            status=data.get("status", ""),
            effort=data.get("effort", ""),
            debt=data.get("debt", ""),
            text_range=text_range,
            flows=flows,
            raw_json=data,
        )


@dataclass
class SonarProject:
    key: str
    name: str
    qualifier: str = "TRK"
    visibility: str = "public"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SonarProject":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            qualifier=data.get("qualifier", "TRK"),
            visibility=data.get("visibility", "public"),
        )
