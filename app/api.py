from typing import Annotated

from elasticsearch_dsl import Search
from fastapi import FastAPI, HTTPException, Query

from app.config import CONFIG
from app.postprocessing import load_result_processor
from app.query import build_search_query
from app.utils import connection, get_logger

logger = get_logger()

app = FastAPI()
connection.get_connection()


result_processor = load_result_processor(CONFIG)


@app.get("/product/{barcode}")
def get_product(barcode: str):
    results = (
        Search(index=CONFIG.index.name)
        .query("term", code=barcode)
        .extra(size=1)
        .execute()
    )
    results_dict = [r.to_dict() for r in results]

    if not results_dict:
        raise HTTPException(status_code=404, detail="Barcode not found")

    product = results_dict[0]
    return product


@app.get("/search")
def search(
    q: str,
    langs: Annotated[list[str] | None, Query()] = None,
    num_results: int = 10,
    projection: Annotated[list[str] | None, Query()] = None,
    sort_by: str | None = None,
):
    langs = set(langs or ["en"])
    query = build_search_query(
        q=q, langs=langs, num_results=num_results, config=CONFIG, sort_by=sort_by
    )
    results = query.execute()
    results_dict = [r.to_dict() for r in results]

    if result_processor is not None:
        results_dict = [result_processor.process(r) for r in results_dict]

    if projection:
        projection_set = set(projection)
        results_dict = [
            dict((k, v) for k, v in r.items() if k in projection_set)
            for r in results_dict
        ]
    return {
        "results": results_dict,
        "debug": {
            "query": query.to_dict(),
        },
    }
