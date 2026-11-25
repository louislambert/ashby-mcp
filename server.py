# /// script
# dependencies = ["mcp>=1.27 ,<2", "httpx>=0.27"]
# requires-python = ">=3.11"
# ///
"""Ashby MCP Server v2 — rebuilt from scratch with correct API contracts."""

import asyncio
import base64
import json
import os
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# ---------------------------------------------------------------------------
# Ashby HTTP client
# ---------------------------------------------------------------------------

BASE_URL = "https://api.ashbyhq.com"


def _auth_headers() -> dict[str, str]:
    key = os.environ.get("ASHBY_API_KEY", "")
    encoded = base64.b64encode(f"{key}:".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


_http: httpx.Client | None = None


def http() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(base_url=BASE_URL, headers=_auth_headers(), timeout=30)
    return _http


def ashby(endpoint: str, body: dict | None = None) -> dict:
    r = http().post(endpoint, json=body or {})
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Tool definitions — one dict per tool keeps it easy to maintain
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    # ── Candidates ────────────────────────────────────────────────────────
    {
        "name": "search_candidates",
        "description": "Search candidates by name and/or email. Returns up to 100 results.",
        "endpoint": "/candidate.search",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Candidate name to search"},
                "email": {"type": "string", "description": "Candidate email to search"},
            },
        },
    },
    {
        "name": "get_candidate",
        "description": "Get a single candidate by ID. Returns full candidate object including applicationIds.",
        "endpoint": "/candidate.info",
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Candidate ID (UUID)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_candidates",
        "description": "List candidates with cursor-based pagination.",
        "endpoint": "/candidate.list",
        "schema": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor from previous response"},
                "limit": {"type": "integer", "description": "Max results (default/max 100)"},
                "createdAfter": {"type": "integer", "description": "Unix epoch ms — only candidates created after this"},
            },
        },
    },
    {
        "name": "create_candidate",
        "description": "Create a new candidate in Ashby.",
        "endpoint": "/candidate.create",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name (required)"},
                "email": {"type": "string", "description": "Primary email"},
                "phoneNumber": {"type": "string", "description": "Phone number"},
                "linkedInUrl": {"type": "string", "description": "LinkedIn profile URL"},
                "githubUrl": {"type": "string", "description": "GitHub profile URL"},
                "website": {"type": "string", "description": "Personal website URL"},
                "sourceId": {"type": "string", "description": "Source UUID"},
                "creditedToUserId": {"type": "string", "description": "User UUID to credit"},
            },
            "required": ["name"],
        },
    },
    # ── Jobs ───────────────────────────────────────────────────────────────
    {
        "name": "search_jobs",
        "description": "Search jobs by title. Returns matching job objects.",
        "endpoint": "/job.search",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Job title to search (required)"},
                "requisitionId": {"type": "string", "description": "Requisition ID to filter"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_job",
        "description": "Get a single job by ID.",
        "endpoint": "/job.info",
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Job ID (UUID)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_jobs",
        "description": "List all jobs with optional filters.",
        "endpoint": "/job.list",
        "schema": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
                "includeArchived": {"type": "boolean", "description": "Include archived jobs (default false)"},
            },
        },
    },
    {
        "name": "create_job",
        "description": "Create a new job. Requires teamId and locationId (UUIDs). Use list_departments/list_locations to find IDs.",
        "endpoint": "/job.create",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Job title"},
                "teamId": {"type": "string", "description": "Team/department UUID (required)"},
                "locationId": {"type": "string", "description": "Location UUID (required)"},
                "defaultInterviewPlanId": {"type": "string", "description": "Interview plan UUID"},
            },
            "required": ["title", "teamId", "locationId"],
        },
    },
    # ── Applications ──────────────────────────────────────────────────────
    {
        "name": "list_applications",
        "description": "List applications. Filter by status and/or jobId. To find applications for a specific candidate, use get_candidate to get their applicationIds.",
        "endpoint": "/application.list",
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter: Hired, Archived, Active, or Lead",
                    "enum": ["Hired", "Archived", "Active", "Lead"],
                },
                "jobId": {"type": "string", "description": "Filter by job UUID"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
                "expand": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Expand related objects, e.g. ['openings']",
                },
            },
        },
    },
    {
        "name": "get_application",
        "description": "Get a single application by ID.",
        "endpoint": "/application.info",
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Application ID (UUID)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "create_application",
        "description": "Create a new application for a candidate on a job.",
        "endpoint": "/application.create",
        "schema": {
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "Candidate UUID"},
                "jobId": {"type": "string", "description": "Job UUID"},
                "sourceId": {"type": "string", "description": "Source UUID"},
                "interviewPlanId": {"type": "string", "description": "Interview plan UUID (defaults to job's plan)"},
                "interviewStageId": {"type": "string", "description": "Interview stage UUID or 'FirstPreInterviewScreen'"},
                "creditedToUserId": {"type": "string", "description": "User UUID to credit"},
            },
            "required": ["candidateId", "jobId"],
        },
    },
    # ── Interview Schedules & Events ──────────────────────────────────────
    {
        "name": "list_interview_schedules",
        "description": "List interview schedules. Returns schedules with interviewEvents showing interviewer assignments, times, and feedback status.",
        "endpoint": "/interviewSchedule.list",
        "schema": {
            "type": "object",
            "properties": {
                "applicationId": {"type": "string", "description": "Filter by application UUID"},
                "interviewStageId": {"type": "string", "description": "Filter by interview stage UUID"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_interview_events",
        "description": "List interview events for a schedule. Returns event details including interviewers, times, and feedback form info.",
        "endpoint": "/interviewEvent.list",
        "schema": {
            "type": "object",
            "properties": {
                "interviewScheduleId": {"type": "string", "description": "Schedule UUID (required)"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
                "expand": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["interviewScheduleId"],
        },
    },
    # ── Interviews & Plans ────────────────────────────────────────────────
    {
        "name": "list_interviews",
        "description": "List interview definitions (not events). Each interview has a feedbackFormDefinitionId.",
        "endpoint": "/interview.list",
        "schema": {
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean", "description": "Include archived (default false)"},
                "includeNonSharedInterviews": {"type": "boolean", "description": "Include job-specific interviews (default false)"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_interview_plan",
        "description": "List interview plan stages and their feedback form definitions.",
        "endpoint": "/interviewPlan.list",
        "schema": {
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    # ── Feedback Forms ────────────────────────────────────────────────────
    {
        "name": "get_feedback_form_definition",
        "description": "Get the structure/fields of a feedback form. Returns field paths, types, selectableValues — everything needed to build a submission.",
        "endpoint": "/feedbackFormDefinition.info",
        "schema": {
            "type": "object",
            "properties": {
                "feedbackFormDefinitionId": {"type": "string", "description": "Form definition UUID"},
            },
            "required": ["feedbackFormDefinitionId"],
        },
    },
    {
        "name": "list_feedback_form_definitions",
        "description": "List all feedback form definitions in the workspace.",
        "endpoint": "/feedbackFormDefinition.list",
        "schema": {
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    # ── Application Feedback ──────────────────────────────────────────────
    {
        "name": "list_application_feedback",
        "description": "List all submitted feedback/scorecards for an application.",
        "endpoint": "/applicationFeedback.list",
        "schema": {
            "type": "object",
            "properties": {
                "applicationId": {"type": "string", "description": "Application UUID (required)"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["applicationId"],
        },
    },
    {
        "name": "submit_application_feedback",
        "description": "Submit interview feedback for an application.\n\nTop-level fields (alongside feedbackForm):\n  - formDefinitionId (required)\n  - applicationId (required)\n  - interviewEventId (optional \u2014 requires userId when set)\n  - userId (optional \u2014 required when interviewEventId is set)\n\nfeedbackForm must contain a \"fieldSubmissions\" array:\n  [{\"path\": \"<field.path>\", \"value\": <typed_value>}, ...]\n\nValue formats by field type:\n  - RichText:         {\"type\": \"PlainText\", \"value\": \"text\"}\n  - Score:            {\"score\": N} where N is 1-4\n  - ValueSelect:      \"string\" matching selectableValues[].value\n  - MultiValueSelect: [\"string\", ...] matching selectableValues[].value\n  - Boolean:          true/false\n  - String/Phone:     \"string\"\n  - Number:           integer\n  - Date:             \"YYYY-MM-DD\"\n  - Email:            \"email@example.com\"",
        "endpoint": "/applicationFeedback.submit",
        "schema": {
            "type": "object",
            "properties": {
                "formDefinitionId": {"type": "string", "description": "Feedback form definition UUID (required)"},
                "applicationId": {"type": "string", "description": "Application UUID (required)"},
                "interviewEventId": {"type": "string", "description": "Interview event UUID (optional \u2014 requires userId)"},
                "userId": {"type": "string", "description": "User UUID (required when interviewEventId is set)"},
                "feedbackForm": {
                    "type": "object",
                    "description": "Must contain fieldSubmissions array",
                    "properties": {
                        "fieldSubmissions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "value": {},
                                },
                                "required": ["path", "value"],
                            },
                        },
                    },
                    "required": ["fieldSubmissions"],
                },
            },
            "required": ["formDefinitionId", "applicationId", "feedbackForm"],
        },
    },
    # ── Lookup helpers ────────────────────────────────────────────────────
    {
        "name": "list_locations",
        "description": "List all hiring locations. Useful to get locationId for job creation.",
        "endpoint": "/location.list",
        "schema": {
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_departments",
        "description": "List all departments. Useful to get teamId for job creation (Ashby calls departments 'teams' in job.create).",
        "endpoint": "/department.list",
        "schema": {
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean"},
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_users",
        "description": "List all users in the Ashby workspace.",
        "endpoint": "/user.list",
        "schema": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_sources",
        "description": "List all candidate sources.",
        "endpoint": "/source.list",
        "schema": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
]

# Build a lookup: tool name -> (endpoint, schema)
_TOOL_MAP = {t["name"]: t for t in TOOLS}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("ashby-v2")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=t["name"], description=t["description"], inputSchema=t["schema"])
        for t in TOOLS
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    tool = _TOOL_MAP.get(name)
    if not tool:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    endpoint = tool["endpoint"]

    try:
        result = ashby(endpoint, arguments)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        return [types.TextContent(type="text", text=text)]
    except httpx.HTTPStatusError as exc:
        body = exc.response.text if exc.response else str(exc)
        return [types.TextContent(type="text", text=f"Ashby API error ({exc.response.status_code}): {body}")]
    except Exception as exc:
        return [types.TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="ashby-v2",
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
