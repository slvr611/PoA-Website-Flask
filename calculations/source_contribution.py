from dataclasses import dataclass, field


@dataclass
class SourceContribution:
    label: str
    source_type: str
    modifiers: dict = field(default_factory=dict)
