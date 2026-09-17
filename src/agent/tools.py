"""Anthropic tool-use schema for the discovery agent's decide step.

Each tool is one possible action the agent can take. Forcing structured tool
calls (rather than parsing free-text instructions) is what makes the
discover -> act loop reliable enough to turn into a recorded artifact: every
decision the model makes is already a typed, unambiguous action.
"""

TOOLS = [
    {
        "name": "click_element",
        "description": "Click an interactive element from the numbered observation list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index from the observation."},
                "reason": {"type": "string", "description": "Why this click moves toward the goal."},
            },
            "required": ["index", "reason"],
        },
    },
    {
        "name": "fill_field",
        "description": "Type a value into a text input from the numbered observation list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "value": {"type": "string", "description": "The literal text to type."},
                "param_ref": {
                    "type": "string",
                    "description": (
                        "If this value corresponds to one of the declared input parameters for this "
                        "capability, name it here (e.g. 'member_id') so the recorded artifact stores a "
                        "reusable {{param}} reference instead of this literal value. Omit if not applicable."
                    ),
                },
                "reason": {"type": "string"},
            },
            "required": ["index", "value", "reason"],
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option in a <select> dropdown from the numbered observation list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "value": {"type": "string", "description": "The option's value attribute or visible text."},
                "param_ref": {"type": "string", "description": "Same meaning as in fill_field."},
                "reason": {"type": "string"},
            },
            "required": ["index", "value", "reason"],
        },
    },
    {
        "name": "navigate",
        "description": "Go directly to a URL on the target app, if no on-page control gets you there.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["url", "reason"],
        },
    },
    {
        "name": "extract_output",
        "description": "Record a visible value as one of this capability's declared outputs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "output_name": {"type": "string", "description": "Must match one of the declared output params."},
                "index": {"type": "integer", "description": "Element index whose text contains the value."},
                "reason": {"type": "string"},
            },
            "required": ["output_name", "index", "reason"],
        },
    },
    {
        "name": "finish_success",
        "description": "Declare the goal achieved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "checkpoint_index": {
                    "type": "integer",
                    "description": "Element index that proves the goal state was reached (for the replay checkpoint).",
                },
                "summary": {"type": "string"},
            },
            "required": ["checkpoint_index", "summary"],
        },
    },
    {
        "name": "finish_failure",
        "description": "Give up: the goal cannot be reached from here.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Stop and request human intervention because you cannot safely determine the next action.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]
