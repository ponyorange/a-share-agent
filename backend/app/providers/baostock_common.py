"""Shared BaoStock session / ResultSet helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd


@contextmanager
def session() -> Iterator[Any]:
    import baostock as bs

    lg = bs.login()
    if getattr(lg, "error_code", "0") != "0":
        raise RuntimeError(
            f"BaoStock 登录失败: {getattr(lg, 'error_code', '?')} "
            f"{getattr(lg, 'error_msg', '')}"
        )
    try:
        yield bs
    finally:
        try:
            bs.logout()
        except Exception:
            pass


def result_to_df(rs: Any) -> pd.DataFrame:
    err = getattr(rs, "error_code", None)
    if err not in (None, "0", 0):
        raise RuntimeError(
            f"BaoStock 查询失败: {err} {getattr(rs, 'error_msg', '')}"
        )
    get_data = getattr(rs, "get_data", None)
    if callable(get_data):
        try:
            df = get_data()
            if isinstance(df, pd.DataFrame):
                return df
        except Exception:
            pass
    rows: list[list[Any]] = []
    while getattr(rs, "error_code", "0") == "0" and rs.next():
        rows.append(rs.get_row_data())
    fields = list(getattr(rs, "fields", []) or [])
    if not fields and rows:
        fields = [f"c{i}" for i in range(len(rows[0]))]
    return pd.DataFrame(rows, columns=fields)
