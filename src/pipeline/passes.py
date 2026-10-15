from __future__ import annotations

from dataclasses import dataclass

from config.agent_prompts import (
    EXPLORATION_PROMPT,
    EXTRACTION_AGENT_PROMPT,
    VALIDATION_PROMPT,
)


@dataclass
class PassConfig:
    prompt: str
    pass_name: str
    max_iterations: int = 15


PASS1_CONFIG = PassConfig(
    prompt=EXPLORATION_PROMPT,
    pass_name="layout_analysis",
)

PASS2_CONFIG = PassConfig(
    prompt=EXTRACTION_AGENT_PROMPT,
    pass_name="data_extraction",
    max_iterations=20,
)

PASS3_CONFIG = PassConfig(
    prompt=VALIDATION_PROMPT,
    pass_name="validation",
    max_iterations=15,
)

ALL_PASSES = [PASS1_CONFIG, PASS2_CONFIG, PASS3_CONFIG]
