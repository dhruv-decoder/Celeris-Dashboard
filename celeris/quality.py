"""Collects every data issue we find so it can be written to a machine-readable report.

Each entry answers: which file, which column, what kind of problem, how many rows,
what we did about it, and a few before -> after examples.
"""
import json
from dataclasses import dataclass, field, asdict

import pandas as pd


@dataclass
class Issue:
    file: str
    column: str
    issue: str
    category: str        # error_fixed | error_dropped | inferred | real_event | master_data | info
    rows: int
    action: str
    evidence: str = ""
    examples: list = field(default_factory=list)


class QualityLog:
    def __init__(self):
        self.issues: list[Issue] = []
        self.profiles: dict = {}

    def add(self, file, column, issue, category, rows, action, evidence="", before=None, after=None, n_examples=3):
        """Record an issue. `before`/`after` are aligned Series used to show examples."""
        rows = int(rows)
        if rows == 0:
            return
        examples = []
        if before is not None:
            idx = list(before.index[:n_examples])
            for i in idx:
                ex = {"row": int(i) if isinstance(i, (int,)) or hasattr(i, "__int__") else str(i),
                      "before": _plain(before.loc[i])}
                if after is not None:
                    ex["after"] = _plain(after.loc[i])
                examples.append(ex)
        self.issues.append(Issue(file, column, issue, category, rows, action, evidence, examples))

    def profile(self, name, stage, df):
        """Store a small snapshot of a table (rows, distinct values, blanks) before/after cleaning."""
        self.profiles.setdefault(name, {})[stage] = {
            "rows": int(len(df)),
            "columns": {
                c: {"distinct": int(df[c].nunique(dropna=True)),
                    "blank": int((df[c].isna() | (df[c].astype(str).str.strip() == "")).sum())}
                for c in df.columns
            },
        }

    def table(self):
        return pd.DataFrame([{k: v for k, v in asdict(i).items() if k != "examples"} for i in self.issues])

    def write(self, path):
        payload = {
            "summary": {
                "issues": len(self.issues),
                "rows_touched": int(sum(i.rows for i in self.issues)),
                "by_category": self.table().groupby("category")["rows"].sum().astype(int).to_dict(),
                "by_file": self.table().groupby("file")["rows"].sum().astype(int).to_dict(),
            },
            "issues": [asdict(i) for i in self.issues],
            "profiles": self.profiles,
        }
        path.write_text(json.dumps(payload, indent=2, default=str))


def _plain(v):
    if isinstance(v, pd.Series):
        return {k: _plain(x) for k, x in v.items()}
    if pd.isna(v) if not isinstance(v, (list, dict)) else False:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v
