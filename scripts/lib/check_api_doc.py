"""Fails when docs/api.md and the stack's template disagree on the API endpoints.

The template, scripts/template.yaml, is the source of truth. Three kinds of declaration are read:
- a SAM event of type Api or HttpApi, whose Method and Path name one REST route;
- an OpenAPI DefinitionBody, whose paths and their method keys name REST routes;
- an AWS::ApiGatewayV2::Route, whose RouteKey names one WebSocket route.

docs/api.md names a REST route in backticks as `METHOD /path` and a WebSocket route as its route
key in backticks (`$connect`). A route the template declares and the document does not name, or a
REST route the document names and the template does not declare, fails the check.

Run from scripts/test.sh: python3 scripts/lib/check_api_doc.py <repo root>
"""

import re
import sys
from pathlib import Path

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
EVENT_TYPE = re.compile(r"^(\s+)Type: (?:Api|HttpApi)\s*$")
EVENT_FIELD = re.compile(r"^\s+(Method|Path): (\S+)\s*$")
OPENAPI_PATHS = re.compile(r"^(\s+)paths:\s*$")
OPENAPI_PATH = re.compile(r"^(\s+)(/\S*):\s*$")
OPENAPI_METHOD = re.compile(r"^\s+(get|post|put|patch|delete):\s*$")
WEBSOCKET_ROUTE = re.compile(r"^\s+RouteKey: ['\"]?([^'\"\s]+)['\"]?\s*$")
DOC_REST_ROUTE = re.compile(r"`(" + "|".join(HTTP_METHODS) + r") (/[^`\s]*)`")
DOC_CODE_SPAN = re.compile(r"`([^`]+)`")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def sam_event_routes(lines: list[str]) -> set[str]:
    routes = set()
    for i, line in enumerate(lines):
        match = EVENT_TYPE.match(line)
        if not match:
            continue
        depth = len(match.group(1))
        fields = {}
        for following in lines[i + 1 :]:
            if following.strip() and indent_of(following) < depth:
                break
            field = EVENT_FIELD.match(following)
            if field:
                fields[field.group(1)] = field.group(2)
        routes.add(f"{fields['Method'].upper()} {fields['Path']}")
    return routes


def openapi_routes(lines: list[str]) -> set[str]:
    routes = set()
    for i, line in enumerate(lines):
        match = OPENAPI_PATHS.match(line)
        if not match:
            continue
        depth = len(match.group(1))
        path = None
        for following in lines[i + 1 :]:
            if following.strip() and indent_of(following) <= depth:
                break
            path_match = OPENAPI_PATH.match(following)
            if path_match:
                path = path_match.group(2)
                continue
            method = OPENAPI_METHOD.match(following)
            if method and path is not None:
                routes.add(f"{method.group(1).upper()} {path}")
    return routes


def template_routes(template: Path) -> tuple[set[str], set[str]]:
    lines = template.read_text().splitlines()
    rest = sam_event_routes(lines) | openapi_routes(lines)
    websocket = {m.group(1) for line in lines if (m := WEBSOCKET_ROUTE.match(line))}
    return rest, websocket


def main(root: Path) -> int:
    rest, websocket = template_routes(root / "scripts" / "template.yaml")
    doc_path = root / "docs" / "api.md"
    if not doc_path.is_file():
        print(f"{doc_path} not found; the template declares {len(rest | websocket)} routes")
        return 1
    doc = doc_path.read_text()
    documented_rest = {f"{m.group(1)} {m.group(2)}" for m in DOC_REST_ROUTE.finditer(doc)}
    code_spans = set(DOC_CODE_SPAN.findall(doc))
    problems = [f"undocumented in docs/api.md: `{r}`" for r in sorted(rest - documented_rest)]
    problems += [
        f"undocumented in docs/api.md: WebSocket route `{r}`" for r in sorted(websocket - code_spans)
    ]
    problems += [
        f"docs/api.md names `{r}`, which scripts/template.yaml does not declare"
        for r in sorted(documented_rest - rest)
    ]
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
