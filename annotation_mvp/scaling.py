"""Davidson measurement and auditable quality control; no implicit penalty."""
import itertools
import json
import math
from collections import Counter, defaultdict

import networkx as nx
import numpy as np
from scipy.linalg import helmert
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax
from scipy.stats import binomtest, chi2, rankdata, spearmanr

from annotation_mvp.protocol import GENRES, atomic_json, pair_key, quota_deficits, schedule, write_csv

def wilson(success,total,z=1.959963984540054):
    success,total=int(success),int(total)
    if not total: return dict(n=0,success=success,rate=None,ci=None)
    p=success/total; d=1+z*z/total
    center=(p+z*z/(2*total))/d
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return dict(n=total,success=success,rate=p,ci=[center-half,center+half])

def observed_graph(names,rows,tie_arcs=False):
    g=nx.DiGraph(); g.add_nodes_from(names)
    for r in rows:
        a,b=r['img_left'],r['img_right']
        if a not in g or b not in g: continue
        if r['choice']=='tie':
            if tie_arcs: g.add_edge(a,b); g.add_edge(b,a)
        else:
            w=r['chosen_image']; g.add_edge(w,b if w==a else a)
    return g

def connectivity(names,rows):
    strict=observed_graph(names,rows)
    ties=observed_graph(names,rows,True)
    comps=list(nx.strongly_connected_components(strict))
    condensation=nx.condensation(strict,comps)
    ordered=list(nx.topological_sort(condensation));cuts=[];left=set()
    for c in ordered[:-1]:
        left.update(comps[c]); right=set(names)-left
        forward=sum(a in left and b in right for a,b in strict.edges)
        backward=sum(b in left and a in right for a,b in strict.edges)
        cuts.append(dict(left_size=len(left),right_size=len(right),forward_edges=forward,backward_edges=backward,
                         suggested_pair=[sorted(comps[c])[0],sorted(comps[ordered[ordered.index(c)+1]])[0]],
                         note='补判候选；结果方向不可预定，不能保证消除分离'))
    return dict(strict_strong=bool(names) and nx.is_strongly_connected(strict),
                tie_augmented_strong=bool(names) and nx.is_strongly_connected(ties),
                components=[sorted(c) for c in comps],component_edges=list(condensation.edges),
                one_way_cuts=cuts,
                note='胜负图不含平局边；另报把平局当双向支持的图。Davidson仍须检查参数边界与Hessian。')

def fit(names,rows,half_ties=False):
    if len(names)<2: return dict(ok=False,reason='少于两张图片')
    idx={n:i for i,n in enumerate(names)}
    valid=[r for r in rows if r['img_left'] in idx and r['img_right'] in idx]
    graph=observed_graph(names,valid,tie_arcs=True)
    if not nx.is_strongly_connected(graph): return dict(ok=False,reason='响应支持图不强连通；不存在有限的全局无惩罚估计')
    left=np.array([idx[r['img_left']] for r in valid]); right=np.array([idx[r['img_right']] for r in valid])
    outcomes=np.array([0 if r['choice']=='left' else 1 if r['choice']=='right' else 2 for r in valid])
    n=len(names); basis=helmert(n,full=False).T
    has_ties=bool(np.any(outcomes==2)) and not half_ties
    if np.all(outcomes==2) and not half_ties: return dict(ok=False,reason='全部为平局；平局参数发散，不能给出有限联合MLE')
    design=(basis[left]-basis[right])/2
    dimensions=n-1+int(has_ties)
    def objective(theta):
        delta=np.einsum('ij,j->i',design,theta[:n-1])
        if has_ties:
            logits=np.column_stack([delta,-delta,np.full(len(delta),theta[-1])])
            target=np.eye(3)[outcomes]
        else:
            logits=np.column_stack([delta,-delta])
            target=np.column_stack([np.where(outcomes==0,1,np.where(outcomes==2,.5,0)),
                                    np.where(outcomes==1,1,np.where(outcomes==2,.5,0))])
        p=softmax(logits,axis=1)
        loss=float(np.sum(logsumexp(logits,axis=1)-np.sum(target*logits,axis=1)))
        grad_s=np.einsum('ij,i->j',design,(p[:,0]-target[:,0])-(p[:,1]-target[:,1]))
        grad=np.r_[grad_s,np.sum(p[:,2]-target[:,2])] if has_ties else grad_s
        return loss,grad
    result=minimize(objective,np.zeros(dimensions),jac=True,method='BFGS',options={'gtol':1e-7,'maxiter':2000})
    loss,gradient=objective(result.x)
    if not np.isfinite(loss) or np.linalg.norm(gradient,np.inf)>2e-4 or np.max(np.abs(result.x))>35:
        return dict(ok=False,reason='无惩罚优化未收敛或参数趋于边界',optimizer_message=str(result.message))
    # Central differences of the analytic gradient, jointly including log(nu).
    hessian=np.zeros((dimensions,dimensions))
    for j in range(dimensions):
        step=1e-4*max(1,abs(result.x[j])); dx=np.zeros(dimensions); dx[j]=step
        hessian[:,j]=(objective(result.x+dx)[1]-objective(result.x-dx)[1])/(2*step)
    hessian=(hessian+hessian.T)/2; eigen=np.linalg.eigvalsh(hessian)
    if eigen[0]<=1e-8 or eigen[-1]/eigen[0]>1e12: return dict(ok=False,reason='约束空间Hessian非正定或病态；不输出虚假标准误')
    cov_theta=np.linalg.inv(hessian)
    covariance=np.einsum('ij,jk,lk->il',basis,cov_theta[:n-1,:n-1],basis,optimize=False)
    scores=np.einsum('ij,j->i',basis,result.x[:n-1])
    if not np.all(np.isfinite(covariance)) or not np.all(np.isfinite(scores)):
        return dict(ok=False,reason='非有限数值结果，停止发布标度')
    return dict(ok=True,names=names,scores=scores,covariance=covariance,se=np.sqrt(np.maximum(0,np.diag(covariance))),
                nu=float(np.exp(result.x[-1])) if has_ties else 0.,nll=loss,
                tie_parameter_boundary=not has_ties and not half_ties,n_observations=len(valid))

def prediction(model,a,b):
    index={n:i for i,n in enumerate(model['names'])}
    delta=(model['scores'][index[a]]-model['scores'][index[b]])/2
    if model['nu']==0: return np.r_[softmax([delta,-delta]),0.]
    return softmax([delta,-delta,math.log(model['nu'])])

def reliability(tasks,answers):
    pairs=[(answers[t['repeat_of_task_id']],answers[t['task_id']]) for t in tasks
           if t['is_hidden_repeat'] and t['task_id'] in answers and t['repeat_of_task_id'] in answers]
    strict=sum(a['chosen_image']==b['chosen_image'] for a,b in pairs)
    loose=sum(a['chosen_image']==b['chosen_image'] or a['choice']=='tie' or b['choice']=='tie' for a,b in pairs)
    out=dict(strict=wilson(strict,len(pairs)),loose=wilson(loose,len(pairs)),
             loose_definition='同一获胜图片，或至少一次为平局；不称为完全一致')
    if pairs:
        a=strict/len(pairs)
        if a>=.5: low=(1+math.sqrt(2*a-1))/2
        elif a>=1/3: low=(2+math.sqrt(6*a-2))/6
        else: low=None
        high=(1+math.sqrt(6*a-2))/3 if a>=1/3 else None
        out['homogeneous_iid_reference']=[low,high]
    out['limitation']='区间仅在重复独立同分布且各对共享响应概率结构时解释；平均一致率不能确定异质图片对的总体准确率上限。非平局评测另有选择效应。'
    return out

def audit(store):
    plan=store.plan(2)
    if not plan: return dict(passed=False,issues=['第二轮尚未生成'])
    tasks=plan['tasks']; answers=store.answers
    missing=[t['task_id'] for t in tasks if t['task_id'] not in answers]
    names=[n for n in store.paths if store.r1[n]['bin']!='NA']
    core=[r for r in answers.values() if r['img_left'] in names and r['img_right'] in names]
    g=nx.Graph(); g.add_nodes_from(names); g.add_edges_from((r['img_left'],r['img_right']) for r in core)
    positions={t['task_id']:i for i,t in enumerate(tasks)}
    hidden=[t for t in tasks if t['is_hidden_repeat'] and t['task_id'] in answers]
    swaps={t['pair_key'] for t in tasks if t['is_swap_subset'] and t['repeat_of_task_id'] and t['task_id'] in answers}
    base_count=sum(t['scope']=='core' and not t['repeat_of_task_id'] for t in tasks)
    cooldown=[]; last={}
    for i,t in enumerate(tasks):
        for n in (t['img_left'],t['img_right']):
            if i-last.get(n,-1000)<=5: cooldown.append(i)
            last[n]=i
    spacing=[positions[t['task_id']]-positions[t['repeat_of_task_id']]-1 for t in hidden]
    cross=[]
    for field,levels in GENRES.items():
        for a,b in itertools.combinations(levels,2):
            count=sum({store.genres[x][field],store.genres[y][field]}=={a,b} for x,y in g.edges)
            cross.append(dict(variable=field,a=a,b=b,n=count))
    deficits=quota_deficits(list(store.paths),{n:r['bin'] for n,r in store.r1.items()},store.genres)
    conn=connectivity(names,core)
    issues=[]
    if missing: issues.append(f'第二轮尚余{len(missing)}题')
    if min(dict(g.degree()).values(),default=0)<8: issues.append('存在图片少于8个不同对手')
    if len(hidden)<40 or min(spacing,default=0)<100: issues.append('隐藏重复不足40或间隔不足100')
    if len(swaps)<max(100,math.ceil(.1*base_count)): issues.append('位置互换完成数不足')
    if cooldown: issues.append('存在5题内重复出现')
    if deficits: issues.append('题材格不足20张')
    if any(r['n']<25 for r in cross): issues.append('跨类别边不足25')
    if not conn['strict_strong']: issues.append('实际胜负图不强连通，需要有界补判或分量报告')
    return dict(passed=not issues,issues=issues,connectivity=conn,hidden_completed=len(hidden),
                hidden_min_gap=min(spacing,default=None),swaps_completed=len(swaps),swap_required=max(100,math.ceil(.1*base_count)),
                min_unique_opponents=min(dict(g.degree()).values(),default=0),cross_edges=cross,cell_deficits=deficits,
                hidden_positions=[positions[t['task_id']] for t in hidden],cooldown_violations=cooldown,
                note='连通且无桥只保证存在一种强连通定向，不保证实际判断出来的那个定向强连通。')

def adaptive(store,model,count=250):
    if store.plan(3): return dict(ok=True,message='第三轮已存在；不追加无限补判')
    plan=store.plan(2)
    if not plan or any(t['task_id'] not in store.answers for t in plan['tasks']): raise ValueError('第二轮尚未完成')
    names=[x for x in store.paths if store.r1[x]['bin']!='NA']; rows=list(store.answers.values())
    g=observed_graph(names,rows); comps=list(nx.strongly_connected_components(g))
    component={x:i for i,c in enumerate(comps) for x in c}
    rng=np.random.default_rng(store.config['seed']+3)
    used=Counter(r['pair_key'] for r in rows)
    candidates=[]
    index={n:i for i,n in enumerate(names)}
    if model['ok']:
        index={n:i for i,n in enumerate(model['names'])}
        ranked=sorted(names,key=lambda n:model['scores'][index[n]])
    else:
        dag=nx.condensation(g,comps)
        ranked=[n for c in nx.topological_sort(dag) for n in sorted(comps[c])]
    rank={n:i for i,n in enumerate(ranked)}
    for a,b in itertools.combinations(names,2):
        connectivity_priority=component[a]!=component[b]
        precision=0.; close=0.
        if model['ok']:
            i,j=index[a],index[b]; cov=model['covariance']
            se=math.sqrt(max(0,cov[i,i]+cov[j,j]-2*cov[i,j]))
            precision=max(model['se'][i],model['se'][j])
            close=se/(abs(model['scores'][i]-model['scores'][j])+se+1e-12)
        # Without identifiable global scores, use SCC topological order, not fabricated scores.
        candidates.append((a,b,connectivity_priority,precision,close,1/(1+abs(rank[a]-rank[b]))+rng.random()*.001))
    selected={}; degree=Counter()
    # Three budgets. Connectivity candidates are meaningful even when no global MLE exists.
    for key in (lambda t:(t[2],-used[pair_key(t[0],t[1])],t[5]),
                lambda t:(t[3],t[4],t[5]),lambda t:(t[4],t[3],t[5])):
        added=0
        for a,b,*_ in sorted(candidates,key=key,reverse=True):
            k=pair_key(a,b)
            if k in selected or max(degree[a],degree[b])>=max(8,count//8): continue
            selected[k]=dict(a=a,b=b,kind='adaptive',scope='core'); degree[a]+=1; degree[b]+=1
            added+=1
            if added>=math.ceil(count/3) or len(selected)>=count: break
        if len(selected)>=count: break
    tasks=schedule(list(selected.values()),store.config['seed']+30,3,0,0)
    # Avoid repeats across the round boundary too.
    tail=plan['tasks'][-5:]
    for shift in range(len(tasks)):
        candidate=tasks[shift:]+tasks[:shift]
        last={}; valid=True
        for pos,t in enumerate(tail+candidate):
            for n in (t['img_left'],t['img_right']):
                if pos-last.get(n,-1000)<=5: valid=False; break
                last[n]=pos
            if not valid: break
        if valid: tasks=candidate; break
    else: raise ValueError('第三轮边界无法满足冷却间隔；未写入')
    atomic_json(store.root/'round3_tasks.json',dict(seed=store.config['seed']+30,tasks=tasks,
                reason='按精度、近邻、强连通分量间连接生成；不存在全局MLE时不伪造SE或分数',max_rounds=3))
    return dict(ok=True,total=len(tasks))

def grouped_pair_rows(rows):
    groups=defaultdict(list)
    for r in rows: groups[r['pair_key']].append(r)
    return groups

def triangle_summary(names,rows,model,simulations=1000,seed=1):
    groups=grouped_pair_rows(rows); pairs=list(groups); idx={k:i for i,k in enumerate(pairs)}
    g=nx.Graph(); g.add_nodes_from(names)
    g.add_edges_from(tuple(json.loads(k)) for k in pairs)
    triples=[]
    for a in sorted(g):
        for b in sorted(x for x in g[a] if x>a):
            for c in sorted(x for x in set(g[a])&set(g[b]) if x>b):
                triples.append([idx[pair_key(a,b)],idx[pair_key(b,c)],idx[pair_key(a,c)]])
    if not triples: return dict(n_complete_triangles=0,denominator=0,rate=None)
    triples=np.array(triples,dtype=int)
    def majority(rs):
        a,b=json.loads(rs[0]['pair_key']); count=Counter(r['chosen_image'] for r in rs)
        if count[a]>count[b]+count['']: return 0
        if count[b]>count[a]+count['']: return 1
        return 2
    def rate(outcomes):
        t=outcomes[triples]; valid=np.all(t!=2,axis=1)
        cycles=valid & (t[:,0]==t[:,1]) & (t[:,0]!=t[:,2])
        return int(cycles.sum()),int(valid.sum())
    observed=np.array([majority(groups[k]) for k in pairs]); cycles,den=rate(observed)
    result=dict(n_complete_triangles=len(triples),cycles=cycles,denominator=den,rate=cycles/den if den else None,
                aggregation='同一对采用严格多数；平局或无严格多数不进入非平局三元组分母')
    if model['ok']:
        rng=np.random.default_rng(seed); null=[]
        probs=np.array([prediction(model,*json.loads(k)) for k in pairs]); repeats=np.array([len(groups[k]) for k in pairs])
        for _ in range(simulations):
            votes=np.array([rng.multinomial(n,p) for n,p in zip(repeats,probs)])
            chosen=np.where(votes[:,0]>repeats/2,0,np.where(votes[:,1]>repeats/2,1,2))
            c,d=rate(chosen)
            if d: null.append(c/d)
        result.update(simulations=simulations,null_valid=len(null),null_quantiles=np.quantile(null,[.025,.5,.975]).tolist() if null else None,
                      empirical_percentile=float(np.mean(np.array(null)<=cycles/den)) if null and den else None)
    return result

def clusters(model):
    if not model['ok']: return dict(available=False)
    s=model['scores']; cov=model['covariance']; n=len(s); output=[]
    for k in range(3,min(7,n)+1):
        centers=np.quantile(s,np.linspace(0,1,k)); membership=np.zeros(n,dtype=int)
        for _ in range(100):
            new=np.argmin(abs(s[:,None]-centers),axis=1)
            if any(not np.any(new==j) for j in range(k)): break
            updated=np.array([s[new==j].mean() for j in range(k)])
            membership=new
            if np.max(abs(updated-centers))<1e-10: centers=updated; break
            centers=updated
        groups=[np.flatnonzero(membership==j) for j in range(k)]
        if any(len(x)==0 for x in groups): output.append(dict(k=k,feasible=False,reason='空簇')); continue
        groups.sort(key=lambda g:s[g].mean()); diffs=[]
        for a,b in zip(groups,groups[1:]):
            w=np.zeros(n); w[a]=-1/len(a); w[b]=1/len(b)
            se=float(np.sqrt(max(0,w@cov@w))); diff=float(w@s)
            diffs.append(dict(difference=diff,se_difference=se,significant=diff>1.96*se))
        output.append(dict(k=k,feasible=all(d['significant'] for d in diffs),adjacent=diffs))
    span=float(np.ptp(s)); med=float(np.median(model['se']))
    return dict(range=span,median_se=med,resolvable_ratio={str(m):span/(m*med) if med else None for m in (2,3,4)},
                candidates=output,max_feasible_k=max((x['k'] for x in output if x['feasible']),default=None),
                limitation='探索性分组：在同一批估计上选簇再检验，未校正选择与多重比较；不能作为已验证等级数。')

def evaluate(rows,model):
    if not model['ok']: return dict(available=False,reason=model.get('reason'))
    hits=0; nll=0.; used=0
    for r in rows:
        if r['img_left'] not in model['names'] or r['img_right'] not in model['names']: continue
        p=prediction(model,r['img_left'],r['img_right']); y={'left':0,'right':1,'tie':2}[r['choice']]
        hits+=int(np.argmax(p)==y); nll-=math.log(max(p[y],1e-15)); used+=1
    return dict(available=True,accuracy=wilson(hits,used),mean_log_loss=nll/used if used else None)

def conditional_scale_lrt(names,rows,genres,field,shapes,levels):
    """Conditional sensitivity test: fix independently estimated within-group shapes.

    Otherwise free item scores and group noise are not separately identifiable.
    Geometric-mean noise is explicitly defined for comparisons crossing two groups.
    """
    if any(np.ptp([shapes[n] for n in names if genres[n][field]==level])<1e-8 for level in levels):
        return dict(available=False,reason='存在常数的组内形状；无法识别该组的噪声尺度')
    idx={n:i for i,n in enumerate(names)}; gi=np.array([levels.index(genres[n][field]) for n in names])
    left=np.array([idx[r['img_left']] for r in rows]);right=np.array([idx[r['img_right']] for r in rows])
    y=np.array([{'left':0,'right':1,'tie':2}[r['choice']] for r in rows])
    base=np.array([shapes[n] for n in names]);basis=helmert(len(levels),full=False).T
    ties=bool(np.any(y==2)); k=len(levels)
    def solve(separate):
        dimensions=k-1+(k if separate else 1)+int(ties)
        def objective(t):
            offset=basis@t[:k-1]
            noise=t[k-1:k-1+k] if separate else np.repeat(t[k-1],k)
            scale=np.exp((noise[gi[left]]+noise[gi[right]])/2)
            d=(base[left]+offset[gi[left]]-base[right]-offset[gi[right]])/(2*scale)
            logits=np.column_stack([d,-d,np.repeat(t[-1],len(y))]) if ties else np.column_stack([d,-d])
            return float(np.sum(logsumexp(logits,axis=1)-logits[np.arange(len(y)),y]))
        r=minimize(objective,np.zeros(dimensions),method='BFGS',options={'maxiter':1500,'gtol':1e-5})
        return r
    null=solve(False);alternative=solve(True)
    if not null.success or not alternative.success or max(np.max(abs(null.x)),np.max(abs(alternative.x)))>25:
        return dict(available=False,reason='条件噪声尺度模型未收敛或边界估计')
    statistic=max(0,2*(null.fun-alternative.fun))
    return dict(available=True,lr=float(statistic),df=k-1,p_value=float(chi2.sf(statistic,k-1)),n=len(rows),
                null_nll=float(null.fun),alternative_nll=float(alternative.fun),
                limitation='固定组内形状的条件探索性LRT；跨组噪声定义为两组尺度的几何均值。未传播第一阶段估计与重复相关性，不能当作确认性DIF检验。')

def invariance(names,rows,genres,seed):
    """Pair-key holdout; within-group shapes fixed before estimating cross-group offsets."""
    rng=np.random.default_rng(seed); out={}
    for field in GENRES:
        groups={level:[x for x in names if genres[x][field]==level] for level in GENRES[field]}
        pairrows=grouped_pair_rows(rows)
        internal=[k for k in pairrows if genres[json.loads(k)[0]][field]==genres[json.loads(k)[1]][field]]
        cross=[k for k in pairrows if k not in set(internal)]
        rng.shuffle(internal); rng.shuffle(cross)
        intra_test=set(internal[:len(internal)//2]); cross_test=set(cross[:len(cross)//2])
        train_inside=[r for k in internal if k not in intra_test for r in pairrows[k]]
        train_cross=[r for k in cross if k not in cross_test for r in pairrows[k]]
        fits={level:fit(members,train_inside) for level,members in groups.items()}
        failed={level:m.get('reason') for level,m in fits.items() if not m['ok']}
        if failed:
            out[field]=dict(available=False,reason='组内训练图不足以识别独立标度；不借测试边修补',groups=failed,
                            train_within=len(train_inside),train_cross=len(train_cross),heldout_within_pairs=len(intra_test),heldout_cross_pairs=len(cross_test))
            continue
        levels=list(groups); group_index={x:levels.index(level) for level,members in groups.items() for x in members}
        shapes={x:float(m['scores'][i]) for m in fits.values() for i,x in enumerate(m['names'])}
        b=helmert(len(levels),full=False).T
        def objective(theta):
            offset=b@theta[:-1]; nu=np.exp(theta[-1]); loss=0.
            for r in train_cross:
                a,c=r['img_left'],r['img_right']; delta=(shapes[a]+offset[group_index[a]]-shapes[c]-offset[group_index[c]])/2
                logits=[delta,-delta,math.log(nu)]
                loss+=logsumexp(logits)-logits[{'left':0,'right':1,'tie':2}[r['choice']]]
            return loss
        result=minimize(objective,np.zeros(len(levels)),method='BFGS')
        if not result.success or np.max(abs(result.x))>30:
            out[field]=dict(available=False,reason='固定组内形状后的跨组平移估计未收敛'); continue
        offsets=b@result.x[:-1]
        model=dict(ok=True,names=names,scores=np.array([shapes[x]+offsets[group_index[x]] for x in names]),nu=float(np.exp(result.x[-1])))
        a=evaluate([r for k in intra_test for r in pairrows[k]],model)
        c=evaluate([r for k in cross_test for r in pairrows[k]],model)
        out[field]=dict(available=True,within=a,cross=c,offsets=dict(zip(levels,offsets.tolist())),
                        limitation='预测差异为模型失配信号；难度组成和噪声不同可造成差异，不能单独确认DIF。区间按判断计，重复对相关性需谨慎。',
                        noise_scale_lrt=conditional_scale_lrt(names,train_cross,genres,field,shapes,levels))
    return out

def analyze(store,simulations=1000):
    plan=store.plan(2)
    if not plan: raise ValueError('第二轮尚未生成')
    if any(t['task_id'] not in store.answers for t in plan['tasks']): raise ValueError('第二轮未完成，不能分析')
    p3=store.plan(3)
    if p3 and any(t['task_id'] not in store.answers for t in p3['tasks']): raise ValueError('第三轮尚未完成')
    names=[x for x in store.paths if store.r1[x]['bin']!='NA']
    rows=[r for r in store.answers.values() if r['img_left'] in names and r['img_right'] in names]
    checks=audit(store)
    # Strict winner connectivity is the requested gate; fit SCCs if the gate remains unmet.
    model=fit(names,rows) if checks['connectivity']['strict_strong'] else dict(ok=False,reason='实际胜负图不强连通：仅输出分量内标度；全局标度不发布')
    nonconnectivity_issues=[x for x in checks['issues'] if '不强连通' not in x]
    if nonconnectivity_issues: raise ValueError('验收未通过，停止正式分析：'+'；'.join(nonconnectivity_issues))
    bt=fit(names,rows,True) if model['ok'] else dict(ok=False)
    rel=reliability(plan['tasks'],store.answers)
    first_answers={}
    for event in getattr(store,'events',[]):
        if event['type']=='comparison':first_answers.setdefault(event['task_id'],event)
    if first_answers:
        rel['first_submission_sensitivity']=reliability(plan['tasks'],first_answers)
        rel['correction_count']=sum(e['type']=='undo' or 'replaces_event_index' in e for e in store.events)
        rel['correction_note']='主值使用最终有效答案；同时报告首次提交的一致率。回退后的重答不是新增隐藏重复，不增加40对分母。'
    swaprows=[r for r in rows if r['is_swap_subset']]
    decisive=[r for r in swaprows if r['choice']!='tie']
    position=wilson(sum(r['choice']=='left' for r in decisive),len(decisive))
    position.update(p_value=float(binomtest(position['success'],position['n'],.5).pvalue) if decisive else None,
                    tie_rate=sum(r['choice']=='tie' for r in swaprows)/len(swaprows) if swaprows else None,
                    all_presentations=len(swaprows),limitation='同一对两次作答相关，普通二项检验仅为描述性参考')
    paired=grouped_pair_rows(swaprows); signs=[]
    for rs in paired.values():
        if len(rs)==2 and all(r['choice']!='tie' for r in rs):
            signs.append(sum(r['choice']=='left' for r in rs)-1)
    effective=[v for v in signs if v]
    position['paired_swap_test']=dict(complete_non_tie_pairs=len(signs),informative_pairs=len(effective),
        both_left=sum(v>0 for v in effective),both_right=sum(v<0 for v in effective),
        p_value=float(binomtest(sum(v>0 for v in effective),len(effective),.5).pvalue) if effective else None)
    rt=np.array([r['rt_ms'] for r in store.answers.values()])
    scoremap=dict(zip(model.get('names',[]),model.get('scores',[])))
    btmap=dict(zip(bt.get('names',[]),bt.get('scores',[])))
    drift={}
    session_order=sorted({r['session_id'] for r in rows},key=lambda s:min(r['timestamp'] for r in rows if r['session_id']==s))
    for session in session_order:
        rs=[r for r in rows if r['session_id']==session and r['choice']!='tie' and r['img_left'] in btmap]
        drift[session]=wilson(sum(btmap[r['chosen_image']]>min(btmap[r['img_left']],btmap[r['img_right']]) for r in rs),len(rs))
        drift[session]['first_timestamp']=min(r['timestamp'] for r in rows if r['session_id']==session)
    bins={'N':0,'M':1,'T':2}; ordered=[n for n in names if store.r1[n]['bin'] in bins and n in btmap]
    rankcorr=float(spearmanr([bins[store.r1[n]['bin']] for n in ordered],[btmap[n] for n in ordered]).statistic) if len(ordered)>2 and len({btmap[n] for n in ordered})>1 and len({store.r1[n]['bin'] for n in ordered})>1 else None
    if rankcorr is not None and not np.isfinite(rankcorr): rankcorr=None
    compare=float(spearmanr(model['scores'],bt['scores']).statistic) if model['ok'] and bt['ok'] and np.ptp(model['scores'])>0 and np.ptp(bt['scores'])>0 else None
    if compare is not None and not np.isfinite(compare): compare=None
    na_distribution={field:{level:dict(total=sum(store.genres[n][field]==level for n in store.paths),
                        na=sum(store.genres[n][field]==level and store.r1[n]['bin']=='NA' for n in store.paths))
                        for level in levels} for field,levels in GENRES.items()}
    na_rows=[r for r in store.answers.values() if r not in rows]
    invariant=invariance(names,rows,store.genres,store.config['seed']+99) if model['ok'] else dict(available=False,reason='全局标度不可识别')
    flagged=[]
    for field,result in invariant.items():
        if not isinstance(result,dict) or not result.get('available'): continue
        inside=result['within'].get('accuracy',{}).get('ci');cross=result['cross'].get('accuracy',{}).get('ci')
        result['cross_prediction_warning']=bool(inside and cross and cross[1]<inside[0])
        if result['cross_prediction_warning']: flagged.append(field)
    if flagged:
        localrows=[]
        for field in flagged:
            for level in GENRES[field]:
                members=[n for n in names if store.genres[n][field]==level];local=fit(members,rows)
                for i,n in enumerate(members):
                    localrows.append(dict(variable=field,group=level,image=n,score=float(local['scores'][i]) if local['ok'] else '',
                                          se=float(local['se'][i]) if local['ok'] else '',status='仅组内可比较' if local['ok'] else local['reason']))
        write_csv(store.root/'genre_scores.csv',['variable','group','image','score','se','status'],localrows)
    counts=Counter(n for r in rows for n in (r['img_left'],r['img_right']))
    score_rows=[dict(image=n,score=float(scoremap[n]) if n in scoremap and not flagged else '',se=float(model['se'][model['names'].index(n)]) if n in scoremap and not flagged else '',
                     n_comparisons=counts[n],round1_bin=store.r1[n]['bin']) for n in store.paths]
    write_csv(store.root/'bt_scores.csv',['image','score','se','n_comparisons','round1_bin'],score_rows)
    components=[]
    if not model['ok']:
        component_rows=[]
        for i,members in enumerate(checks['connectivity']['components']):
            local=fit(members,rows); components.append(dict(component=i,size=len(members),ok=local['ok'],reason=local.get('reason','')))
            if local['ok']:
                component_rows.extend(dict(component=i,image=n,score=float(local['scores'][j]),se=float(local['se'][j])) for j,n in enumerate(members))
        write_csv(store.root/'component_scores.csv',['component','image','score','se'],component_rows)
    tiegraph=nx.Graph(); tiegraph.add_nodes_from(names)
    grouped=grouped_pair_rows(rows)
    for k,rs in grouped.items():
        if any(r['choice']=='tie' for r in rs): tiegraph.add_edge(*json.loads(k))
    groups={x:i for i,c in enumerate(nx.connected_components(tiegraph)) for x in c}
    ties=[]
    for k,rs in grouped.items():
        count=sum(r['choice']=='tie' for r in rs)
        if count:
            a,b=json.loads(k); ties.append(dict(img_a=a,img_b=b,n_ties=count,n_comparisons=len(rs),candidate_group=groups[a],status='候选；未发现差异，不保证传递'))
    write_csv(store.root/'ties.csv',['img_a','img_b','n_ties','n_comparisons','candidate_group','status'],ties)
    human=[dict(img_a=json.loads(r['pair_key'])[0],img_b=json.loads(r['pair_key'])[1],winner=r['chosen_image']) for r in rows if r['choice']!='tie']
    write_csv(store.root/'pairs_human.csv',['img_a','img_b','winner'],human)
    subgroup_quality={}
    for field in GENRES:
        subgroup_quality[field]={}
        for label,same in [('within',True),('cross',False)]:
            subset=[r for r in rows if (store.genres[r['img_left']][field]==store.genres[r['img_right']][field])==same]
            subgroup_quality[field][label]=dict(ties=wilson(sum(r['choice']=='tie' for r in subset),len(subset)),
                cycles=triangle_summary(names,subset,model,simulations,store.config['seed']+7),
                note='较高平局率不区分势均力敌与无从比较')
    report=dict(audit=checks,model=dict(ok=model['ok'],reason=model.get('reason'),nu=model.get('nu'),
                  cross_genre_warnings=flagged,global_scores_published=model['ok'] and not flagged,
                  score_scale='Davidson log-strength, sum-to-zero; model-dependent interval scale',components=components),
                self_consistency=rel,position_bias=position,drift=drift,
                reaction_time=dict(n=len(rt),quantiles_ms=np.quantile(rt,[0,.1,.25,.5,.75,.9,1]).tolist(),below_500=int(sum(rt<500)),fraction_below_500=float(np.mean(rt<500))),
                coarse_scale=dict(spearman=rankcorr,n=len(ordered),u=sum(store.r1[n]['bin']=='U' for n in names),na=len(store.paths)-len(names)),
                davidson_bt_spearman=compare,clusters=clusters(model),cycles=triangle_summary(names,rows,model,simulations,store.config['seed']),
                invariance=invariant,
                subgroup_quality=subgroup_quality,
                na_distribution=na_distribution,na_probe=dict(n=len(na_rows),ties=sum(r['choice']=='tie' for r in na_rows),
                    per_image={n:dict(n=sum(n in (r['img_left'],r['img_right']) for r in na_rows),
                                     wins=sum(r['chosen_image']==n for r in na_rows),
                                     ties=sum(n in (r['img_left'],r['img_right']) and r['choice']=='tie' for r in na_rows))
                               for n in plan['na_probe_images']},note='强制比较结果不能独自证明适用或不适用'),
                quantile_cuts=store.config['quantile_cuts'],export=dict(non_tie_rows=len(human),excluded_ties=len(rows)-len(human),excluded_na_probe=len(na_rows),
                winner_encoding='original filename of selected image',note='保留重复观测；下游划分必须按pair_key/母图隔离，避免重复对泄漏；非平局条件化改变评测总体'))
    atomic_json(store.root/'analysis.json',report)
    lines=['# 通透标注分析','', '仅描述本标注者与本题库；未发现差异不等于相同。',
           '跨题材边用于检验可比性，不按低信息量删除。组间预测误差差异或平局率差异不能独自确认DIF或无从比较。',
           '重复的一致性不是独立性的证明；间隔只能减轻记忆影响。', '',
           '全局标度状态：'+('可估计，但跨题材预测出现警报；仅发布genre_scores.csv组内标度' if flagged else '可估计；适用范围仍须结合题材诊断' if model['ok'] else model['reason']), '',
           'bt_scores.csv 的 score/se 来自 Davidson；BT仅作对照。不可估计值留空，绝不填零。','']
    titles={'audit':'验收与分离结构','model':'标度模型','self_consistency':'自一致率与有条件的参考上限',
            'position_bias':'位置偏好','drift':'场次漂移','reaction_time':'反应时间','coarse_scale':'粗分与标度一致性',
            'davidson_bt_spearman':'Davidson 与 BT 的秩相关','clusters':'探索性分组','cycles':'循环三元组',
            'invariance':'跨题材预测诊断','subgroup_quality':'组内与跨组的平局及循环',
            'na_distribution':'不适用的题材分布','na_probe':'不适用图片的探索性比较',
            'quantile_cuts':'像素分组的三分位切点','export':'下游数据口径'}
    for key,value in report.items(): lines.extend(['## '+titles.get(key,key),'','```json',json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),'```',''])
    if model['ok']:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(11,4)); order=np.argsort(model['scores'])
        axes[0].errorbar(np.arange(len(names)),model['scores'][order],yerr=1.96*model['se'][order],fmt='.',alpha=.6)
        axes[0].set(xlabel='Ordered image index',ylabel='Davidson score (95% Wald CI)')
        axes[1].hist(rt/1000,bins=40); axes[1].set(xlabel='Reaction time (s)',ylabel='Judgments')
        fig.tight_layout(); fig.savefig(store.root/'diagnostics.png',dpi=150); plt.close(fig)
        lines.extend(['![标度与反应时间](diagnostics.png)',''])
    (store.root/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return model,report
