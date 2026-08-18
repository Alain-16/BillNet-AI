UNDERSTANDING_SCHEMA = {
    "name": "receipt_understanding",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["purchase_summary", "purchase_domain", "line_items"],
        "properties": {
            "purchase_summary": {
                "type": "string",
                "description": "One sentence describing what was bought, in business terms.",
            },
            "purchase_domain": {
                "type": "string",
                "description": "Trade or domain, e.g. plumbing, electrical, general. Empty string if unclear.",
            },
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_line", "description", "meaning"],
                    "properties": {
                        "source_line": {"type": "integer"},
                        "description": {"type": "string"},
                        "meaning": {
                            "type": "string",
                            "description": (
                                "Business meaning. Say whether it is a reusable tool, "
                                "a consumable material, protective equipment, a service, "
                                "or storage. Do NOT name an accounting account."
                            ),
                        },
                    },
                },
            },
        },
    },
}

RESOLUTION_SCHEMA = {
    "name": "categorization_resolution",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["vendor_external_id", "project_external_id", "line_items"],
        "properties": {
            # "" means unresolved. Strict mode cannot express nullable cleanly,
            # so empty string is the sentinel and the service converts it.
            "vendor_external_id": {
                "type": "string",
                "description": "external_id of the chosen vendor candidate, or \"\" if none fits.",
            },
            "project_external_id": {
                "type": "string",
                "description": (
                    "external_id of the chosen project, or \"\" if the receipt carries "
                    "no project evidence. Choosing \"\" is CORRECT when there is no evidence."
                ),
            },
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_line", "account_external_id", "rationale"],
                    "properties": {
                        "source_line": {"type": "integer"},
                        "account_external_id": {
                            "type": "string",
                            "description": "external_id of one supplied account candidate, or \"\".",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One short sentence citing the evidence used.",
                        },
                    },
                },
            },
        },
    },
}