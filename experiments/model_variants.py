import pandas as pd, numpy as np, time, lightgbm as lgb, sys
"""First experiment: which training set-up for the global model? (run from the repo root; ~5 min per variant)

  python experiments/model_variants.py l1      # absolute-error objective (the chosen one)
  python experiments/model_variants.py l1adj   # + one-off orders capped, COVID/strike months down-weighted
  python experiments/model_variants.py l2adj   # squared-error objective (aims at the average)

Needs a feature table: python experiments/build_feature_table.py
Results of the 2026-11-24 runs are recorded in experiments/results_2026-11-24.json.
"""
import os
S = "outputs/experiments/"
os.makedirs(S, exist_ok=True)
A=pd.read_parquet(S+'allfeat.parquet')
CAT=['Product_Family','Region','Sales_Channel','Customer_Name','Powertrain']
for c in CAT: A[c]=A[c].astype('category')
scale=A.level_12.where(A.level_12>0, A.order_size_12).fillna(1).clip(lower=1)
A['scale']=scale; A['log_scale']=np.log1p(scale)
REL=['level_3','level_6','level_24','std_12','last_value','order_size_12','same_month_last_year','same_month_2y_ago','level_x_pattern']
for c in REL: A['r_'+c]=A[c]/scale
DROP=set(['origin','target','SKU_ID','y','weight','scale','level_12']+REL)
FEAT=[c for c in A.columns if c not in DROP]
# training adjustments: one-off orders capped, COVID/strike down-weighted (from anomaly register)
reg=pd.read_parquet('outputs/reports/anomaly_register.parquet')
one=reg[reg.type=='One-off bulk order'].set_index(['SKU_ID','Region','Date']).expected
dis=set(zip(*reg[reg.type.isin(['COVID-19 lockdown','Strike at customer plants'])][['SKU_ID','Region','Date']].values.T))
k=list(zip(A.SKU_ID,A.Region,A.target))
A['y_train']=A.y.copy(); cap=pd.Series([one.get(x,np.nan) for x in k],index=A.index); A.loc[cap.notna(),'y_train']=cap[cap.notna()]
A['w_event']=np.where([x in dis for x in k],0.2,1.0)
print('capped',cap.notna().sum(),' downweighted',(A.w_event<1).sum())
def run(name, params, adj=False, rounds=600):
    out=[]
    for O in ['2022-03','2023-03','2024-03','2025-03']:
        tr=A[(A.origin<O)&(A.target<=O)&A.y.notna()&(A.weight>0)]
        te=A[(A.origin==O)&A.y.notna()&(A.weight>0)]
        yt=tr.y_train if adj else tr.y; w=tr.scale*tr.weight*(tr.w_event if adj else 1)
        mdl=lgb.train(params,lgb.Dataset(tr[FEAT],yt/tr.scale,weight=w,categorical_feature=CAT),rounds)
        out.append(te[['origin','SKU_ID','Region','target','h','y','scale','level_x_pattern']].assign(pred=np.clip(mdl.predict(te[FEAT])*te.scale,0,None)))
    o=pd.concat(out); o['method']=name; o.to_parquet(S+f'pred_{name}.parquet'); 
    pr=o.pred.round(); print(name,'WAPE',round((pr-o.y).abs().sum()/o.y.sum(),4),'bias',round((pr.sum()-o.y.sum())/o.y.sum(),4), {g:round((x.pred.round().sum()-x.y.sum())/x.y.sum(),3) for g,x in o.groupby('origin')}); sys.stdout.flush()
B=dict(learning_rate=0.05,num_leaves=63,min_data_in_leaf=200,feature_fraction=0.8,bagging_fraction=0.8,bagging_freq=1,verbose=-1,seed=0,num_threads=8)
which=sys.argv[1]
if which=='l1adj': run('l1adj',{**B,'objective':'l1'},adj=True)
if which=='l2adj': run('l2adj',{**B,'objective':'l2'},adj=True)
if which=='l1': run('l1',{**B,'objective':'l1'},adj=False)
