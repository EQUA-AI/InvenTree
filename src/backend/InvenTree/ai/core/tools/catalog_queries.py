"""Explicit catalog search intent and native page coverage, without model prose."""

from __future__ import annotations

import re

from asgiref.sync import sync_to_async

_FILTERED = re.compile(
    r"^\s*(?:list|show|find)\s+(?:all\s+)?(?:catalog\s+)?parts?\s+matching\s+(?:the\s+keyword\s+)?[\"']?([\w -]{1,100}?)[\"']?\s+with\s+active\s+(?:stock\s+only|in-stock\s+quantity\s+above\s+zero)\s*(?:[.;?!]|$)(?:\s*do not restrict to a Bearings category[.]?)?\s*$",
    re.I,
)

_KEYWORD = re.compile(
    r"^\s*(?:list|show|find)\s+(?:all\s+)?parts?\s+(?:matching|containing)\s+[\"']?([\w -]{1,100}?)[\"']?\s*(?:[.;?!]|$)",
    re.I,
)
_PAGE = re.compile(
    r"^\s*(?:show|list|give)\s+(?:only\s+)?(?:the\s+)?first\s*(\d{1,3})\s+(?:part\s+)?keyword\s+matches\s+for\s+[\"']?([\w -]{1,100}?)[\"']?\s*(?:\s+and\s+state\b|[.;?!]|$)",
    re.I,
)
_CATEGORY = re.compile(
    r"^\s*(?:list|show|find)\s+parts?\s+(?:specifically\s+)?in\s+(?:the\s+)?(.+?)\s+category\s*[.?!]?\s*$",
    re.I,
)
_CATEGORY_ID = re.compile(
    r"^\s*(?:list|show|find)\s+parts?\s+in\s+category\s*(?:id\s*)?[:#]?\s*(\d{1,9})\s*[.?!]?\s*$",
    re.I,
)


def catalog_request(query: str) -> dict | None:
    """Recognize complete read requests; ambiguous filters retain existing planning."""
    from ai.core.tools.capabilities import _write_intent

    if _write_intent(query.casefold()) is not None:
        return None
    from ai.core.read_numbers import normalize_read_numbers

    parsed = normalize_read_numbers(query)
    query = parsed.text
    if parsed.ambiguous:
        return (
            {"unsupported": True}
            if re.match(
                r"\s*(?:list|show|find|give)\s+(?:(?:all|catalog|only|the)\s+)*(?:parts?\b|first\b)",
                query,
                re.I,
            )
            else None
        )
    filtered = _FILTERED.fullmatch(query)
    if filtered:
        return {
            "query": filtered[1].strip(),
            "limit": 100,
            "offset": 0,
            "is_active": True,
            "has_stock": True,
        }
    page = _PAGE.match(query)
    if page:
        if re.search(
            r"\b(?:with|where|stock|active|inactive|only|except|category)\b", page[2], re.I
        ):
            return {"unsupported": True}
        tail = query[page.end() :].strip().lower()
        if tail and not re.fullmatch(
            r"(?:total,?\s*returned count\s*and\s*whether more pages exist)[.?!]?", tail
        ):
            return {"unsupported": True}
        return {"query": page[2].strip(), "limit": min(100, max(1, int(page[1]))), "offset": 0}
    keyword = _KEYWORD.match(query)
    if keyword:
        if re.search(
            r"\b(?:with|where|stock|active|inactive|only|except|category)\b", keyword[1], re.I
        ):
            return {"unsupported": True}
        tail = query[keyword.end() :].strip().lower()
        if tail and not re.fullmatch(
            r"(?:do not (?:add|use) (?:a |any )?category filter)[.?!]?", tail
        ):
            return {"unsupported": True}
        return {"query": keyword[1].strip(), "limit": 100, "offset": 0}
    category_id = _CATEGORY_ID.match(query)
    if category_id:
        return {"category_id": int(category_id[1]), "limit": 100, "offset": 0}
    category = _CATEGORY.match(query)
    if category:
        return {"category_name": category[1].strip(), "limit": 100, "offset": 0}
    return None


async def resolve_catalog_query(query: str, context: dict | None = None) -> dict | None:
    """Read only an explicit catalog request, preserving native page metadata."""
    from ai.core.tools.inventree.read.database import _current_user
    from ai.core.tools.inventree.read.parts import search_parts
    from users.permissions import check_user_role

    request = catalog_request(query)
    if request is None:
        return None
    if request.get("unsupported"):
        return {
            "text": "I could not verify all the requested catalog filters. Specify a keyword, an exact category, or active parts with current stock; no unfiltered result has been substituted.",
            "evidence": {"resolved": False, "filters_verified": False},
        }

    def authorize_category():
        from django.db.models import Q
        from part.models import PartCategory

        user = _current_user()
        if user is None or not check_user_role(user, "part", "view"):
            return {"error": "Catalog results could not be verified with your permissions."}
        if request.get("has_stock") and not check_user_role(user, "stock", "view"):
            return {"error": "Catalog stock results could not be verified with your permissions."}
        if request.get("category_name"):
            rows = PartCategory.objects.filter(
                Q(name__iexact=request["category_name"])
                | Q(pathstring__iexact=request["category_name"])
            )
            if rows.count() != 1:
                if not rows.exists():
                    return {
                        "error": "No current catalog category matches that name. Specify its category ID or full path."
                    }
                choices = "; ".join(
                    f"{r.pathstring} (category {r.pk})" for r in rows.order_by("pk")[:5]
                )
                return {
                    "error": f"Which exact category should I use? {choices}. Specify its category ID or full path."
                }
            request["category_id"] = rows.values_list("pk", flat=True).get()
        if request.get("category_id") is not None:
            row = PartCategory.objects.filter(pk=request["category_id"]).first()
            if row is None:
                return {"error": "That category could not be verified."}
            request["category_name"] = row.pathstring
        return None

    error = await sync_to_async(authorize_category)()
    if error:
        return {
            "text": error["error"],
            "evidence": {"resolved": False, "requires_clarification": True},
        }
    if request.get("has_stock"):
        data = await _filtered_keyword(request)
        if not data.get("resolved"):
            return {
                "text": "The complete keyword population and requested stock filters could not be verified.",
                "evidence": data,
            }
    else:
        data = await search_parts(
            query=request.get("query"),
            category_id=request.get("category_id"),
            is_active=None,
            limit=request["limit"],
            offset=request["offset"],
            include_coverage=True,
        )
    name = (
        f"Keyword {request['query']!r} across catalog parts"
        if "query" in request
        else f"Category {request['category_name']} (ID {request['category_id']})"
    )
    if request.get("has_stock"):
        name += "; active parts with exact current on-hand stock above zero"
    total = data.get("population_count")
    more = data.get("has_more")
    text = f"{name}. Total matches: {total if total is not None else 'unverified'}; returned count: {data['returned_count']}; more pages: {'yes' if more is True else 'no' if more is False else 'unverified'}."
    rows = data["results"][: 3 if (context or {}).get("modality") == "voice" else 100]
    displayed = 0
    for row in rows:
        entry = f" Part {row['pk']}: {row.get('IPN') or '(no IPN)'}; {row['name']}."
        if (context or {}).get("modality") == "voice" and len(text) + len(entry) > 600:
            break
        text += entry
        displayed += 1
    if displayed < data["returned_count"]:
        text += " Additional returned parts are available in chat."
    return {"text": text, "evidence": data}


async def _filtered_keyword(request: dict) -> dict:
    """Page the actual keyword search, then filter fresh exact native stock."""
    from ai.core.tools.inventree.read.parts import search_parts

    ids: set[int] = set()
    total = None
    for offset in range(0, 25_000, 100):
        page = await search_parts(
            query=request["query"], is_active=True, limit=100, offset=offset, include_coverage=True
        )
        count = page.get("population_count")
        rows = page["results"]
        page_ids = [row.get("pk") for row in rows]
        if (
            type(count) is not int
            or count > 25_000
            or (total is not None and count != total)
            or any(type(pk) is not int or pk in ids for pk in page_ids)
            or len(set(page_ids)) != len(page_ids)
        ):
            return {"resolved": False}
        total = count
        ids.update(page_ids)
        if page.get("has_more") is False:
            break
        if not rows:
            return {"resolved": False}
    if total != len(ids) or page.get("has_more") is not False:
        return {"resolved": False}

    def native():
        from decimal import Decimal

        from ai.core.tools.inventree.read.database import _current_user
        from django.db.models import DecimalField, Q, Sum
        from django.db.models.functions import Coalesce
        from part.models import Part
        from stock.models import StockItem
        from users.permissions import check_user_role

        user = _current_user()
        if user is None or not all(
            check_user_role(user, role, "view") for role in ("part", "stock")
        ):
            return {"resolved": False}
        parts = Part.objects.filter(pk__in=ids, active=True)
        if parts.count() != len(ids):
            return {"resolved": False}
        current = StockItem.objects.filter(StockItem.IN_STOCK_FILTER)
        matches = (
            parts
            .annotate(
                total=Coalesce(
                    Sum(
                        "stock_items__quantity", filter=Q(stock_items__pk__in=current.values("pk"))
                    ),
                    Decimal(0),
                    output_field=DecimalField(),
                )
            )
            .filter(total__gt=0)
            .order_by("pk")
        )
        count = matches.count()
        rows = [
            {
                "pk": p.pk,
                "name": p.name,
                "IPN": p.IPN,
                "revision": p.revision,
                "total_in_stock": str(p.total),
            }
            for p in matches[:100]
        ]
        return {
            "resolved": True,
            "results": rows,
            "population_count": count,
            "returned_count": len(rows),
            "complete_population": True,
            "has_more": count > len(rows),
            "evaluated_count": total,
            "applied_filters": {
                "query": request["query"],
                "category_id": None,
                "active": True,
                "current_stock_gt": "0",
            },
        }

    return await sync_to_async(native)()
