import httpx

GITHUB_LIST_PER_PAGE = 100


def fetch_paginated_github_collection(
    client: httpx.Client,
    path: str,
    *,
    headers: dict[str, str],
    collection_key: str,
    params: dict | None = None,
    require_total_count_match: bool = False,
) -> list[dict]:
    items: list[dict] = []
    page = 1
    base_params = dict(params or {})

    while True:
        request_params = {**base_params, "per_page": GITHUB_LIST_PER_PAGE, "page": page}
        response = client.get(path, headers=headers, params=request_params)
        response.raise_for_status()
        payload = response.json()
        page_items = payload.get(collection_key, [])
        items.extend(page_items)

        total_count = payload.get("total_count")
        if isinstance(total_count, int) and len(items) >= total_count:
            break
        if len(page_items) < GITHUB_LIST_PER_PAGE:
            break
        page += 1

    total_count = payload.get("total_count")
    if require_total_count_match and isinstance(total_count, int) and len(items) != total_count:
        raise RuntimeError(
            f"GitHub {path} pagination returned {len(items)} {collection_key}; "
            f"expected total_count={total_count}"
        )
    return items
