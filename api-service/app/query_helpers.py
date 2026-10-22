"""Bound Oracle expression lists without changing selection or ordering."""

from sqlalchemy import false, or_


def ids_in(column, values):
    values = list(dict.fromkeys(values))
    return (
        or_(*(column.in_(values[i : i + 900]) for i in range(0, len(values), 900)))
        if values
        else false()
    )
