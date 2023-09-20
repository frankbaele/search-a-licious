from __future__ import annotations

import json
from typing import Annotated

from elasticsearch_dsl import Q
from fastapi import FastAPI, HTTPException, Query

from app.config import CONFIG
from app.models.product import Product
from app.models.request import AdvancedSearchRequest, AutocompleteRequest
from app.utils import (
    connection,
    constants,
    dict_utils,
    get_logger,
    query_utils,
    response,
)

logger = get_logger()

app = FastAPI()
connection.get_connection()


@app.get("/product/{barcode}")
def get_product(barcode: str):
    results = Product.search().query("term", code=barcode).extra(size=1).execute()
    results_dict = [r.to_dict() for r in results]

    if not results_dict:
        raise HTTPException(status_code=404, detail="Barcode not found")

    product = results_dict[0]
    return product


@app.post("/autocomplete")
def autocomplete(request: AutocompleteRequest):
    if not request.search_fields:
        request.search_fields = constants.AUTOCOMPLETE_FIELDS
    for field in request.search_fields:
        if field not in constants.AUTOCOMPLETE_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid field: {field}",
            )

    match_queries = []
    for field in request.search_fields:
        autocomplete_field_name = f"{field}__autocomplete"
        match_queries.append(
            Q("match", **{autocomplete_field_name: request.text}),
        )

    results = (
        Product.search()
        .query("bool", should=match_queries)
        .extra(
            size=request.get_num_results(),
        )
        .execute()
    )
    resp = response.create_response(results, request.response_fields)
    return resp


def validate_field(field, fields_to_types, valid_types, filter_type):
    try:
        field_value = dict_utils.get_nested_value(field, fields_to_types)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=f"Field {field} does not exist",
        )
    field_type = field_value["type"]
    if field_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Field {field} is not of type {filter_type}",
        )
    return field_type


def create_search_query(request: AdvancedSearchRequest):
    fields_to_types = Product._doc_type.mapping.properties.to_dict()["properties"]
    must_queries = []
    must_not_queries = []
    range_queries = []

    for filter in request.string_filters:
        field = filter.field
        field_type = validate_field(
            field,
            fields_to_types,
            [
                "text",
                "keyword",
            ],
            "string",
        )
        # If it's a text field, use the raw nested field for the eq, ne case
        if field_type == "text" and filter.operator in ["eq", "ne"]:
            field = f"{field}__raw"

        if filter.operator == "eq":
            must_queries.append(
                query_utils.create_query(
                    "term",
                    field,
                    filter.value,
                ),
            )
        elif filter.operator == "ne":
            must_not_queries.append(
                query_utils.create_query("term", field, filter.value),
            )
        elif filter.operator == "like":
            must_queries.append(
                query_utils.create_query(
                    "match",
                    field,
                    filter.value,
                ),
            )

    for filter in request.numeric_filters:
        field = filter.field
        validate_field(field, fields_to_types, ["double"], "numeric")

        if filter.operator == "eq":
            must_queries.append(
                query_utils.create_query(
                    "term",
                    field,
                    filter.value,
                ),
            )
        elif filter.operator == "ne":
            must_not_queries.append(
                query_utils.create_query("term", field, filter.value),
            )
        elif filter.operator == "lt":
            # range_queries.append(query_utils.create_range_query('lt', field, filter.value))
            range_queries.append({field: {"lt": filter.value}})
        elif filter.operator == "gt":
            range_queries.append({field: {"gt": filter.value}})

    for filter in request.date_time_filters:
        field = filter.field
        validate_field(field, fields_to_types, ["date"], "date_time")

        if filter.operator == "lt":
            range_queries.append({field: {"lt": filter.value}})  # type: ignore
        elif filter.operator == "gt":
            range_queries.append({field: {"gt": filter.value}})  # type: ignore

    query = Product.search().query(
        "bool",
        must=must_queries,
        must_not=must_not_queries,
    )
    if range_queries:
        for range_query in range_queries:
            query = query.filter(query_utils.create_range_query(range_query))
    return query.extra(size=request.get_num_results())


@app.get("/search")
def search(
    q: str,
    langs: Annotated[list[str] | None, Query()] = None,
    add_english: bool = True,
    num_results: int = 10,
    projection: Annotated[list[str] | None, Query()] = None,
):
    if langs is None:
        langs = {"en"}
    else:
        langs = set(langs)

    if add_english:
        langs.add("en")

    query = query_utils.build_search_query(q, langs, num_results, CONFIG)
    logger.info("query:\n%s", json.dumps(query.to_dict(), indent=4))
    results = query.execute()
    return response.create_response(results, projection)


@app.post("/advanced-search")
def search(request: AdvancedSearchRequest):
    if (
        not request.string_filters
        and not request.numeric_filters
        and not request.date_time_filters
    ):
        raise HTTPException(
            status_code=400,
            detail="At least one filter must be used",
        )

    results = create_search_query(request).execute()
    return response.create_response(results, request.response_fields)
