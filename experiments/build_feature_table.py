import pandas as pd, numpy as np, time
from celeris.backtest import Data, month, label
from celeris.features import make_features
"""Builds the feature table used by experiments/model_variants.py (every 2nd month as origin + backtest origins)."""
import os
S = "outputs/experiments/"
os.makedirs(S, exist_ok=True)
p=pd.read_parquet('outputs/demand/panel.parquet'); m=pd.read_parquet('outputs/clean/sku_master.parquet'); mac=pd.read_parquet('outputs/clean/macro.parquet'); ev=pd.read_parquet('outputs/clean/events.parquet')
d=Data(p,m)
origins=sorted(set([label(x) for x in range(month('2019-12'),month('2026-10')+1,2)]+['2022-03','2023-03','2024-03','2025-03','2026-10']))
t=time.time(); parts=[]
for o in origins:
    F=make_features(d,month(o),19,mac,ev,m)
    num=F.select_dtypes('float64').columns; F[num]=F[num].astype('float32')
    parts.append(F)
A=pd.concat(parts,ignore_index=True); print(len(origins),'origins', A.shape, round(A.memory_usage(deep=True).sum()/1e9,2),'GB', round(time.time()-t),'s')
A.to_parquet(S+'allfeat.parquet')
