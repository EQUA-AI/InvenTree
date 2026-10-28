"""Provider citation references resolve only to exact authorized tool coordinates."""

import copy
import json
import re


def _value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def bind_citation_references(request, candidate_parser):
    """Annotate observed evidence, never invent or broaden a source candidate."""
    groups = []
    for item in request.get("input", []):
        if _value(item, "type") != "function_call_output":
            continue
        try:
            result = json.loads(_value(item, "output", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and result.get("status") == "ok":
            evidence = result.get("evidence")
            if isinstance(evidence, list) and evidence:
                groups.append(evidence)
    all_groups = groups
    groups = []
    seen_manual_pages = set()
    for group in all_groups:
        selected = []
        for evidence in group:
            if (
                isinstance(evidence, dict)
                and evidence.get("source_type") == "related_attached_manual"
            ):
                revision = evidence.get("revision", "")
                page = re.search(r"#page=(\d+)(?:$|[&#])", str(evidence.get("locator", "")))
                if (
                    isinstance(revision, str)
                    and re.fullmatch(r"[0-9a-fA-F]{64}", revision)
                    and page
                ):
                    marker = (revision.casefold(), int(page.group(1)))
                    valid = candidate_parser([
                        {
                            "type": "function_call_output",
                            "output": json.dumps({"status": "ok", "evidence": [evidence]}),
                        }
                    ])
                    if valid:
                        if marker in seen_manual_pages:
                            continue
                        seen_manual_pages.add(marker)
            selected.append(evidence)
        groups.append(selected)
    interleaved = [
        {
            "type": "function_call_output",
            "output": json.dumps({
                "status": "ok",
                "evidence": [group[offset]],
            }),
        }
        for offset in range(max((len(group) for group in groups), default=0))
        for group in groups
        if offset < len(group)
    ]
    candidates = candidate_parser(interleaved)
    if not candidates:
        bounded = copy.deepcopy(request)
        for item in bounded.get("input", []):
            if _value(item, "type") != "function_call_output":
                continue
            try:
                result = json.loads(_value(item, "output", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(result, dict) and result.get("evidence"):
                result["evidence"] = []
                result["citation_selection_limited"] = True
                item["output"] = json.dumps(result, separators=(",", ":"))
        return bounded, {}
    references = {}
    tuples = {}
    source_order = {}
    for group in all_groups:
        for evidence in group:
            if not isinstance(evidence, dict):
                continue
            key = tuple(
                evidence.get(name)
                for name in (
                    "source_type",
                    "id",
                    "revision",
                    "as_of",
                    "authorization_class",
                    "locator",
                )
            )
            if all(isinstance(value, str) and value for value in key):
                source_order.setdefault(key, len(source_order) + 1)
    for candidate in candidates:
        fields = candidate["properties"]
        coordinates = {
            key: fields[key]["const"]
            for key in (
                "source_type",
                "source_id",
                "source_revision",
                "as_of",
                "authorization_class",
            )
        }
        coordinates["locator"] = {
            "field": fields["locator"]["properties"]["field"]["const"],
            "page": None,
            "chunk": None,
        }
        key = (
            coordinates["source_type"],
            coordinates["source_id"],
            coordinates["source_revision"],
            coordinates["as_of"],
            coordinates["authorization_class"],
            coordinates["locator"]["field"],
        )
        reference = f"SRC{source_order[key]}"
        if reference in references and references[reference] != coordinates:
            raise ValueError("Ambiguous citation reference")
        references[reference] = coordinates
        tuples[
            coordinates["source_type"],
            coordinates["source_id"],
            coordinates["source_revision"],
            coordinates["as_of"],
            coordinates["authorization_class"],
            coordinates["locator"]["field"],
        ] = reference
    bounded = copy.deepcopy(request)
    schema = bounded["text"]["format"]["schema"]
    schema["properties"]["evidence"]["items"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "citation_id": {"type": "string", "enum": list(references)},
            "claim": {"type": "string"},
        },
        "required": ["citation_id", "claim"],
    }
    for name in ("EvidenceEntry", "EvidenceLocator"):
        schema.get("$defs", {}).pop(name, None)
    for item in bounded["input"]:
        if _value(item, "type") != "function_call_output":
            continue
        try:
            result = json.loads(_value(item, "output", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        selected = []
        original_evidence = result.get("evidence", [])
        for evidence in original_evidence:
            if not isinstance(evidence, dict):
                continue
            key = tuple(
                evidence.get(name)
                for name in (
                    "source_type",
                    "id",
                    "revision",
                    "as_of",
                    "authorization_class",
                    "locator",
                )
            )
            if result.get("status") == "ok" and key in tuples:
                evidence["citation_id"] = tuples[key]
                selected.append(evidence)
        result["evidence"] = selected
        if len(selected) != len(original_evidence):
            result["citation_selection_limited"] = True
        item["output"] = json.dumps(result, separators=(",", ":"))
    bounded["instructions"] = bounded.get("instructions", "") + (
        "\nFor the evidence transport schema, select only the citation_id attached "
        "to a real returned tool evidence row and provide its claim. The server "
        "resolves that reference to the exact original source coordinates. Do not "
        "write, reconstruct, repair or guess source IDs, hashes, timestamps or locators. "
        "Claims are concise factual statements supported by the returned excerpt, "
        "without repeating identity metadata or hashes. If using inline references, "
        "use only the supplied SRC identifiers; never invent an identifier. "
        "Rows without a selectable citation were omitted from this bounded view. "
        "Do not substitute a different page for a claim: omit an unsupported claim "
        "or retrieve the actual supporting passage."
    )
    return bounded, references


def materialize_citation_references(text, references):
    """Expand known references exactly; invalid or mixed entries remain invalid."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
        return text

    def invalid_reference():
        return json.dumps({**payload, "unresolved_citation_reference": True})

    expanded = []
    for evidence in payload["evidence"]:
        if isinstance(evidence, dict) and "citation_id" in evidence:
            if set(evidence) != {"citation_id", "claim"}:
                return invalid_reference()
            reference = evidence["citation_id"]
            if not isinstance(reference, str) or reference not in references:
                return invalid_reference()
            expanded.append({**copy.deepcopy(references[reference]), "claim": evidence["claim"]})
        else:
            expanded.append(evidence)
    payload["evidence"] = expanded

    def label(match):
        coordinates = references[match.group()]
        source = coordinates["source_type"].replace("_", " ") + " #" + coordinates["source_id"]
        page = re.search(r"#page=(\d+)", coordinates["locator"]["field"])
        return source + (", page " + page.group(1) if page else "")

    slots = [
        (payload, field)
        for field in ("detailed_response", "reasoning_summary", "safety_boundary", "spoken_summary")
    ]
    for action in payload.get("recommended_actions", []):
        if isinstance(action, dict):
            slots.extend((action, field) for field in ("title", "detail"))
    questions = payload.get("next_questions")
    if isinstance(questions, list):
        slots.extend((questions, index) for index in range(len(questions)))
    for evidence in expanded:
        if isinstance(evidence, dict):
            slots.append((evidence, "claim"))
    for container, key in slots:
        value = container.get(key) if isinstance(container, dict) else container[key]
        if not isinstance(value, str):
            continue
        if any(reference not in references for reference in re.findall(r"\bSRC\d+\b", value)):
            return invalid_reference()
        container[key] = re.sub(r"\bSRC\d+\b", label, value)
    return json.dumps(payload)


class CitationReferenceResponse:
    """Retain the real provider response alongside its exact reference expansion."""

    def __init__(self, response, references):
        self._response = response
        self.citation_bindings = copy.deepcopy(references)
        self.provider_output_text = _value(response, "output_text", "")
        self.output_text = materialize_citation_references(self.provider_output_text, references)

    def __getattr__(self, name):
        return _value(self._response, name)
