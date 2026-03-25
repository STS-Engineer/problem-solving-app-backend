"""
member_tool.py
══════════════
OpenAI function-calling tool that lets the AI search the AvoMember table
when a user mentions a person's name during a conversation.

Usage: imported by ConversationService and injected into the OpenAI call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.member_directory import MemberDirectory

logger = logging.getLogger(__name__)


# =============================================================================
# TOOL DEFINITION  (passed to OpenAI as tools=[...])
# =============================================================================

MEMBER_LOOKUP_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "lookup_member",
        "description": (
            "Search the company member directory by name or partial name. "
            "Call this whenever the user mentions a person who might be a team "
            "member, responsible owner, approver, or auditor. "
            "Returns matching members with their name, department, role, and contact info."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The name or partial name to search for. "
                        "Examples: 'Jean', 'Dupont', 'Jean Dupont', 'quality manager'."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}


# =============================================================================
# TOOL EXECUTOR
# =============================================================================


def execute_member_lookup(query: str, db: Session) -> str:
    """
    Run the member directory search and return a JSON string
    suitable for sending back to OpenAI as a tool result.
    """
    directory = MemberDirectory(db)
    members = directory.search(query, limit=5)

    if not members:
        return json.dumps(
            {
                "found": 0,
                "members": [],
                "hint": "No match found. Ask the user to clarify the name.",
            }
        )

    results = []
    for m in members:
        results.append(
            {
                "id": m.id,
                "name": m.name,
                "email": m.email or "",
                "department": m.department or "",
                "role": m.role or "",
                "city": m.city or "",
                "office": m.office or "",
            }
        )

    return json.dumps({"found": len(results), "members": results})
